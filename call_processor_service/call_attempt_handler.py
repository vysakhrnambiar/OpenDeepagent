# call_processor_service/call_attempt_handler.py

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Awaitable, Dict, Any

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from database import db_manager
from database.models import Call, CallStatus, TaskStatus
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from common.data_models import RedisDTMFCommand, RedisEndCallCommand
from call_processor_service.asterisk_ami_client import AsteriskAmiClient, AmiAction # Import AmiAction

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class CallAttemptHandler:
    def __init__(self,
                 call_record: Call,
                 task_user_id: int,
                 ami_client: AsteriskAmiClient, # Instance of our AMI client
                 redis_client: RedisClient,
                 unregister_callback: Callable[[int], Awaitable[None]]
                ):
        self.call_record = call_record
        self.task_user_id = task_user_id
        self.ami_client = ami_client
        self.redis_client = redis_client
        self.unregister_callback = unregister_callback

        self.call_id: int = call_record.id
        self.task_id: int = call_record.task_id
        
        self.asterisk_unique_id: Optional[str] = None # Populated by AMI events (Newchannel or OriginateResponse)
        self.asterisk_channel_name: Optional[str] = None # Populated by AMI events
        
        self.call_start_time: Optional[datetime] = None # Time Originate was sent
        self.call_answer_time: Optional[datetime] = None # Time call was answered
        self.call_end_time: Optional[datetime] = None # Time call ended

        self._stop_event = asyncio.Event()
        self._redis_listener_task: Optional[asyncio.Task] = None
        self._ami_event_listener_task_active = False # Flag to manage AMI listener registration

        logger.info(f"[CallAttemptHandler:{self.call_id}] Initialized for Task ID: {self.task_id}, User ID: {self.task_user_id}")

    async def _originate_call(self) -> bool:
        logger.info(f"[CallAttemptHandler:{self.call_id}] Preparing to originate call.")
        
        task = await db_manager.get_task_by_id(self.task_id)
        if not task:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Could not fetch task details for Task ID {self.task_id}. Aborting.")
            await self._update_call_status_db(CallStatus.FAILED_INTERNAL_ERROR, hangup_cause="Task details unavailable")
            return False

        target_phone_number = task.phone_number
        original_number_for_logging = task.phone_number

        if app_config.APP_TEST_MODE:
            target_phone_number = app_config.APP_TEST_MODE_REDIRECT_NUMBER
            logger.warning(f"[CallAttemptHandler:{self.call_id}] TEST MODE ENABLED. Redirecting call for {original_number_for_logging} to {target_phone_number}.")

        # Construct channel based on config. Ensure PJSIP trunk/peer is correctly configured in Asterisk.
        # Example: PJSIP/my_pjsip_trunk/18005551212 or PJSIP/1000 (for an internal extension)
        # Using DEFAULT_CALLER_ID_EXTEN as a placeholder for the outbound endpoint/trunk name here.
        # This needs to match your Asterisk dialplan for outbound calls.
        channel_to_dial = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{app_config.DEFAULT_CALLER_ID_EXTEN}/{target_phone_number}"
        
        # The AudioSocket application needs an extension that sets it up.
        # Often, this is a dedicated extension in extensions.conf, e.g., 'audiosocket_handler'
        # Context: default, Exten: audiosocket_handler, Priority: 1
        # The audiosocket_handler extension would then call the AudioSocket() application.
        # For simplicity, if AudioSocket can be directly called with parameters in the Originate Data field,
        # that might also work, but usually, it's an application called in the dialplan.
        # Let's assume a simple 's' exten in the context for now, and AudioSocket app is configured there.
        # More robustly, you might have a dedicated context for these calls.
        
        originate_action = AmiAction(
            "Originate",
            Channel=channel_to_dial,
            Context=app_config.DEFAULT_ASTERISK_CONTEXT, # Context where the AudioSocket app will be run
            Exten="s", # Extension that runs AudioSocket (or specific handler exten)
            Priority=1,
            Application="AudioSocket", # This tells Asterisk to connect to our AudioSocket server
            Data=f"ws://{app_config.AUDIOSOCKET_HOST}:{app_config.AUDIOSOCKET_PORT}/callaudio/{self.call_id}", # URL for OUR AudioSocket server
            CallerID=f"OpenDeep <{task.phone_number[:10]}>", # Use part of original number or configured CID
            Timeout=30000, # Call timeout in milliseconds
            Async="true", # Make it an async originate
            Variable=f"CALL_ATTEMPT_ID={self.call_id},TASK_ID={self.task_id},USER_ID={self.task_user_id}", # Pass our IDs
            # ActionID is auto-generated by AmiAction class
        )
        
        self.call_start_time = datetime.now()
        response = await self.ami_client.send_action(originate_action)
        
        if response and response.get("Response") == "Success":
            logger.info(f"[CallAttemptHandler:{self.call_id}] Originate command sent successfully to Asterisk for phone: {target_phone_number} (Task Phone: {original_number_for_logging}). Awaiting events.")
            # We don't get UniqueID or Channel immediately from Originate Success.
            # We'll get them from subsequent Newchannel/VarSet/OriginateResponse events.
            await self._update_call_status_db(CallStatus.ORIGINATING)
            return True
        else:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Failed to send Originate command to Asterisk. Response: {response}")
            await self._update_call_status_db(CallStatus.FAILED_ASTERISK_ERROR, hangup_cause=f"Originate failed: {response.get('Message', 'Unknown error')}")
            return False

    async def _handle_redis_command(self, channel: str, command_data_dict: dict):
        logger.debug(f"[CallAttemptHandler:{self.call_id}] Received Redis command on {channel}: {command_data_dict}")
        command_type = command_data_dict.get("command_type")

        if not self.asterisk_channel_name:
            logger.warning(f"[CallAttemptHandler:{self.call_id}] Cannot process Redis command '{command_type}', Asterisk channel name is not yet known.")
            return

        if command_type == RedisDTMFCommand.model_fields['command_type'].default: # "send_dtmf"
            try:
                cmd = RedisDTMFCommand(**command_data_dict)
                logger.info(f"[CallAttemptHandler:{self.call_id}] Processing DTMF command: sending '{cmd.digits}' to channel {self.asterisk_channel_name}")
                dtmf_action = AmiAction("PlayDTMF", Channel=self.asterisk_channel_name, Digit=cmd.digits)
                response = await self.ami_client.send_action(dtmf_action)
                if response and response.get("Response") == "Success":
                    logger.info(f"[CallAttemptHandler:{self.call_id}] DTMF '{cmd.digits}' sent successfully.")
                else:
                    logger.error(f"[CallAttemptHandler:{self.call_id}] Failed to send DTMF '{cmd.digits}'. Response: {response}")
            except Exception as e:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Error processing DTMF command: {e}", exc_info=True)
        
        elif command_type == RedisEndCallCommand.model_fields['command_type'].default: # "end_call"
            try:
                cmd = RedisEndCallCommand(**command_data_dict)
                logger.info(f"[CallAttemptHandler:{self.call_id}] Processing EndCall command for channel {self.asterisk_channel_name}. Reason: {cmd.reason}")
                hangup_action = AmiAction("Hangup", Channel=self.asterisk_channel_name, Cause="16") # Normal Clearing
                response = await self.ami_client.send_action(hangup_action)
                if response and response.get("Response") == "Success":
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Hangup command sent successfully for channel {self.asterisk_channel_name}.")
                    # The AMI "Hangup" event listener will handle the actual call end processing.
                else:
                    logger.error(f"[CallAttemptHandler:{self.call_id}] Failed to send Hangup command. Response: {response}")
            except Exception as e:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Error processing EndCall command: {e}", exc_info=True)
        else:
            logger.warning(f"[CallAttemptHandler:{self.call_id}] Unknown Redis command type: {command_type}")

    async def _listen_for_redis_commands(self):
        redis_channel_pattern = f"call_commands:{self.call_id}" # Direct channel, not pattern
        logger.info(f"[CallAttemptHandler:{self.call_id}] Subscribing to Redis channel: {redis_channel_pattern}")
        try:
            await self.redis_client.subscribe_to_channel(redis_channel_pattern, self._handle_redis_command)
        except asyncio.CancelledError:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener task cancelled.")
        except Exception as e: # Catch broad exception from subscribe_to_channel
            logger.error(f"[CallAttemptHandler:{self.call_id}] Critical error in Redis listener for {redis_channel_pattern}: {e}", exc_info=True)
            if not self.call_end_time: # If call not already ended by other means
                await self._handle_call_ended(
                    hangup_cause="Redis listener failure",
                    call_conclusion="Call terminated due to Redis communication error.",
                    final_status=CallStatus.FAILED_INTERNAL_ERROR
                )
        finally:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener stopped for channel {redis_channel_pattern}.")

    async def _process_ami_event(self, event: Dict[str, Any]):
        """Processes an AMI event. This is registered as a generic listener with ami_client."""
        event_name = event.get("Event")
        unique_id = event.get("UniqueID")
        linked_id = event.get("Linkedid", unique_id) # Some events use Linkedid for the main call leg
        channel = event.get("Channel")
        
        # Filter events:
        # 1. If we know our UniqueID, event must match it or its LinkedID.
        # 2. If we don't know UniqueID yet, some events (like Newchannel after Originate) might give it to us.
        #    The ActionID of our Originate can help correlate early events if present in the event.
        
        # Initial check for relevance if self.asterisk_unique_id is known
        if self.asterisk_unique_id and unique_id != self.asterisk_unique_id and linked_id != self.asterisk_unique_id:
            # This event is not for this call attempt handler
            # logger.debug(f"[CallAttemptHandler:{self.call_id}] Ignoring event for different UniqueID: {unique_id}/{linked_id} vs {self.asterisk_unique_id}")
            return

        logger.info(f"[CallAttemptHandler:{self.call_id}] Processing relevant AMI Event: {event_name}, UniqueID: {unique_id}, LinkedID: {linked_id}, Channel: {channel}, OurUID: {self.asterisk_unique_id}")

        if not self.asterisk_unique_id and event.get("ActionID") and self.originate_action_id and event.get("ActionID") == self.originate_action_id:
             # This event is a response or related event to our Originate action
             if unique_id and event_name in ["OriginateResponse", "Newchannel", "VarSet"]: # Events that might carry the UniqueID
                logger.info(f"[CallAttemptHandler:{self.call_id}] Captured Asterisk UniqueID: {unique_id} and Channel: {channel} from event {event_name} linked to Originate ActionID {self.originate_action_id}.")
                self.asterisk_unique_id = unique_id
                if channel: self.asterisk_channel_name = channel # Prefer channel from event if available
                # Update DB with channel and UUID
                await self._update_call_status_db(self.call_record.status, # Keep current status or update if needed
                                                  asterisk_channel=self.asterisk_channel_name,
                                                  call_uuid=self.asterisk_unique_id)


        if event_name == "Newchannel":
            # This often signifies the creation of the channel leg for the outbound call.
            # We must capture the UniqueID and Channel name if not already known.
            if not self.asterisk_unique_id:
                self.asterisk_unique_id = unique_id
                self.asterisk_channel_name = channel
                logger.info(f"[CallAttemptHandler:{self.call_id}] Newchannel. Asterisk UniqueID: {unique_id}, Channel: {channel}")
                await self._update_call_status_db(CallStatus.DIALING, asterisk_channel=channel, call_uuid=unique_id)
            elif unique_id == self.asterisk_unique_id and channel and not self.asterisk_channel_name:
                # If we had UniqueID but not channel name, update it
                self.asterisk_channel_name = channel
                logger.info(f"[CallAttemptHandler:{self.call_id}] Newchannel. Updated Channel: {channel} for UniqueID: {unique_id}")
                await db_manager.update_call_status(self.call_id, self.call_record.status, asterisk_channel=channel)


        elif event_name == "Dial": # Dial event family (DialBegin, DialEnd)
            sub_event = event.get("SubEvent")
            if sub_event == "Begin":
                logger.info(f"[CallAttemptHandler:{self.call_id}] Dial Begin for channel {event.get('Channel')}, DestChannel: {event.get('DestChannel')}")
                # Potentially update status to ringing if not already
                if self.call_record.status != CallStatus.RINGING:
                     await self._update_call_status_db(CallStatus.RINGING)
            elif sub_event == "End":
                dial_status = event.get("DialStatus")
                logger.info(f"[CallAttemptHandler:{self.call_id}] Dial End for channel {event.get('Channel')}. Status: {dial_status}")
                if dial_status == "ANSWER":
                    if not self.call_answer_time: self.call_answer_time = datetime.now()
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Call Answered (DialEnd:ANSWER).")
                    await self._update_call_status_db(CallStatus.ANSWERED)
                    # AudioSocketHandler will update to LIVE_AI_HANDLING
                elif dial_status in ["NOANSWER", "CANCEL"]:
                    await self._handle_call_ended(hangup_cause=f"DialEnd: {dial_status}", call_conclusion="No answer", final_status=CallStatus.FAILED_NO_ANSWER)
                elif dial_status == "BUSY":
                    await self._handle_call_ended(hangup_cause="DialEnd: BUSY", call_conclusion="Line busy", final_status=CallStatus.FAILED_BUSY)
                elif dial_status == "CONGESTION":
                     await self._handle_call_ended(hangup_cause="DialEnd: CONGESTION", call_conclusion="Network congestion", final_status=CallStatus.FAILED_CONGESTION)
                elif dial_status == "CHANUNAVAIL":
                     await self._handle_call_ended(hangup_cause="DialEnd: CHANUNAVAIL", call_conclusion="Channel unavailable", final_status=CallStatus.FAILED_CHANNEL_UNAVAILABLE)


        elif event_name == "Hangup":
            hangup_cause_code = event.get("Cause", "0") # Default to 0 if not present
            hangup_cause_txt = event.get("Cause-txt", f"Unknown (Code: {hangup_cause_code})")
            logger.info(f"[CallAttemptHandler:{self.call_id}] Hangup event. Cause: {hangup_cause_txt} (Code: {hangup_cause_code})")
            
            # Determine more specific final status based on hangup cause code
            final_status = CallStatus.COMPLETED_SYSTEM_HANGUP # Default
            if hangup_cause_code == "16": # Normal Clearing
                # This could be user hangup, or AI hangup, or successful completion.
                # The call_conclusion should be set by AudioSocketHandler before hangup for AI/user actions
                # If call_conclusion is not set, assume normal completion or other party hangup.
                call_db_record = await db_manager.get_call_by_id(self.call_id) # Fetch latest record
                if call_db_record and call_db_record.status in [CallStatus.COMPLETED_AI_OBJECTIVE_MET, CallStatus.COMPLETED_AI_HANGUP]:
                    final_status = call_db_record.status
                else: # Default if AI didn't explicitly set it to a success/ai_hangup status
                    final_status = CallStatus.COMPLETED_USER_HANGUP 
            elif hangup_cause_code == "17": # User Busy
                final_status = CallStatus.FAILED_BUSY
            elif hangup_cause_code == "1": # Unallocated number
                 final_status = CallStatus.FAILED_INVALID_NUMBER
            # Add more cause code mappings as needed
            
            await self._handle_call_ended(hangup_cause=f"{hangup_cause_txt} (Code: {hangup_cause_code})",
                                          call_conclusion=f"Call ended by Hangup event: {hangup_cause_txt}",
                                          final_status=final_status)

    async def _handle_call_ended(self, hangup_cause: str, call_conclusion: str, final_status: CallStatus):
        if self.call_end_time:
            logger.warning(f"[CallAttemptHandler:{self.call_id}] Call end already processed for UID {self.asterisk_unique_id}.")
            return

        self.call_end_time = datetime.now()
        duration_seconds = None
        # Calculate duration from answer time if available, otherwise from start time
        meaningful_start_time = self.call_answer_time if self.call_answer_time else self.call_start_time
        if meaningful_start_time:
            duration_seconds = int((self.call_end_time - meaningful_start_time).total_seconds())
        
        logger.info(f"[CallAttemptHandler:{self.call_id}] Call ended for UID {self.asterisk_unique_id}. Duration: {duration_seconds}s. Final Status: {final_status.value}. Cause: {hangup_cause}. Conclusion: {call_conclusion}")

        await self._update_call_status_db(final_status,
                                          hangup_cause=hangup_cause,
                                          call_conclusion=call_conclusion,
                                          duration_seconds=duration_seconds)
        
        self._stop_event.set()

        logger.info(f"[CallAttemptHandler:{self.call_id}] STUB: Would notify PostCallAnalyzerService for Call ID {self.call_id}.")
        # Example: await self.redis_client.publish_command("post_call_analysis_queue", {"call_id": self.call_id, "task_id": self.task_id})

        if self.unregister_callback:
            await self.unregister_callback(self.call_id)
        
        # Remove AMI listener specific to this handler
        if self._ami_event_listener_task_active:
            self.ami_client.remove_generic_event_listener(self._process_ami_event)
            self._ami_event_listener_task_active = False
            logger.info(f"[CallAttemptHandler:{self.call_id}] Removed generic AMI event listener for UID {self.asterisk_unique_id}.")

        logger.info(f"[CallAttemptHandler:{self.call_id}] Processing finished for UID {self.asterisk_unique_id}.")

    async def _update_call_status_db(self, status: CallStatus, **kwargs):
        self.call_record.status = status # Update local copy
        await db_manager.update_call_status(self.call_id, status, **kwargs)

    async def manage_call_lifecycle(self):
        logger.info(f"[CallAttemptHandler:{self.call_id}] Starting to manage call lifecycle.")
        
        # Store ActionID of our Originate to correlate early events if UniqueID isn't immediately known
        # This assumes AmiAction generates a unique ActionID and it's accessible
        # For this, AmiAction needs to store its generated ActionID if not provided
        self.originate_action_id = f"originate-{self.call_id}-{datetime.now().timestamp()}" 
        # The AmiAction class already does this by default if no ActionID is passed.
        # We need to ensure the action object used in _originate_call has this ID.
        # For now, this is conceptual. The actual ActionID will be on the AmiAction object.

        origination_success = await self._originate_call()
        if not origination_success:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Origination failed. Aborting lifecycle management.")
            if self.unregister_callback:
                await self.unregister_callback(self.call_id) # Ensure unregistration
            return

        # Register a generic event listener with the AMI client
        # This handler's _process_ami_event will filter for its own UniqueID
        self.ami_client.add_generic_event_listener(self._process_ami_event)
        self._ami_event_listener_task_active = True
        logger.info(f"[CallAttemptHandler:{self.call_id}] Added generic AMI event listener. Will filter for UID {self.asterisk_unique_id}.")

        self._redis_listener_task = asyncio.create_task(self._listen_for_redis_commands())
        # The _ami_event_listener_task is now conceptual; events are pushed by AsteriskAmiClient's main loop

        try:
            # Wait for the call to end, signaled by _stop_event
            # This event will be set by _handle_call_ended (e.g., on Hangup AMI event or Redis command)
            # Or if a listener task fails critically.
            await self._stop_event.wait()
            logger.info(f"[CallAttemptHandler:{self.call_id}] Stop event received. Proceeding to cleanup.")

        except asyncio.CancelledError:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Main lifecycle task cancelled.")
            if not self.call_end_time: # Ensure call is marked as ended if cancelled abruptly
                await self._handle_call_ended(
                    hangup_cause="Lifecycle task cancelled",
                    call_conclusion="Call terminated due to system cancellation.",
                    final_status=CallStatus.FAILED_INTERNAL_ERROR
                )
        except Exception as e:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Unexpected error in manage_call_lifecycle: {e}", exc_info=True)
            if not self.call_end_time:
                await self._handle_call_ended(
                    hangup_cause="Unexpected lifecycle error",
                    call_conclusion="Call terminated due to unexpected system error.",
                    final_status=CallStatus.FAILED_INTERNAL_ERROR
                )
        finally:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Starting final cleanup for lifecycle.")
            # Ensure Redis listener is stopped
            if self._redis_listener_task and not self._redis_listener_task.done():
                logger.info(f"[CallAttemptHandler:{self.call_id}] Cancelling Redis listener task.")
                self._redis_listener_task.cancel()
                try:
                    await self._redis_listener_task
                except asyncio.CancelledError:
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener task successfully cancelled during cleanup.")
                except Exception as e_redis_cancel:
                    logger.error(f"[CallAttemptHandler:{self.call_id}] Error awaiting cancelled Redis listener: {e_redis_cancel}")


            # Unregister the generic AMI event listener if it was active
            if self._ami_event_listener_task_active:
                self.ami_client.remove_generic_event_listener(self._process_ami_event)
                self._ami_event_listener_task_active = False
                logger.info(f"[CallAttemptHandler:{self.call_id}] Removed generic AMI event listener during cleanup for UID {self.asterisk_unique_id}.")

            # Ensure unregistration callback is called if call hasn't been properly ended and unregistered yet
            if not self.call_end_time: # If _handle_call_ended was not reached for some reason
                 logger.warning(f"[CallAttemptHandler:{self.call_id}] Call end not processed before final cleanup. Forcing unregistration.")
                 if self.unregister_callback:
                    await self.unregister_callback(self.call_id)
            
            logger.info(f"[CallAttemptHandler:{self.call_id}] Lifecycle management fully ended for UID {self.asterisk_unique_id}.")