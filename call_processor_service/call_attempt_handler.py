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
from database.models import Call, CallStatus, TaskStatus, CallCreate
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from common.data_models import RedisDTMFCommand, RedisEndCallCommand
from call_processor_service.asterisk_ami_client import AsteriskAmiClient, AmiAction

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class CallAttemptHandler:
    def __init__(self,
                 call_record: Call,
                 task_user_id: int,
                 ami_client: AsteriskAmiClient,
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
        
        self.asterisk_unique_id: Optional[str] = None
        self.asterisk_channel_name: Optional[str] = None
        self.originate_action_id: Optional[str] = None
        
        self.call_start_time: Optional[datetime] = None
        self.call_answer_time: Optional[datetime] = None
        self.call_end_time: Optional[datetime] = None

        self._stop_event = asyncio.Event()
        self._redis_listener_task: Optional[asyncio.Task] = None
        self._ami_event_listener_task_active = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None # For run_in_executor

        logger.info(f"[CallAttemptHandler:{self.call_id}] Initialized for Task ID: {self.task_id}, User ID: {self.task_user_id}")

    async def _originate_call(self) -> bool:
        logger.info(f"[CallAttemptHandler:{self.call_id}] Preparing to originate call.")
        if self._loop is None: self._loop = asyncio.get_running_loop()
        
        task = await self._loop.run_in_executor(None, db_manager.get_task_by_id, self.task_id)
        if not task:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Could not fetch task details for Task ID {self.task_id}. Aborting.")
            await self._update_call_status_db(CallStatus.FAILED_INTERNAL_ERROR, hangup_cause="Task details unavailable")
            return False

        target_phone_number = task.phone_number
        original_number_for_logging = task.phone_number

        if app_config.APP_TEST_MODE:
            target_phone_number = app_config.APP_TEST_MODE_REDIRECT_NUMBER
            logger.warning(f"[CallAttemptHandler:{self.call_id}] TEST MODE ENABLED. Redirecting call for {original_number_for_logging} to {target_phone_number}.")

        channel_to_dial = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{app_config.DEFAULT_CALLER_ID_EXTEN}/{target_phone_number}"
        #dial_string = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{target_phone_number}" 
        
        # This is the CRITICAL CHANGE: The Data field for AudioSocket
        full_audiosocket_uri_with_call_id = f"ws://{app_config.AUDIOSOCKET_HOST}:{app_config.AUDIOSOCKET_PORT}/callaudio/{self.call_id}"
        logger.info(f"[CallAttemptHandler:{self.call_id}] AudioSocket URI will be: {full_audiosocket_uri_with_call_id}")
        
        # In call_processor_service/call_attempt_handler.py, inside _originate_call method

        # Determine the channel to originate FROMsend_action
        # This could be a specific local channel if you have one set up for app-originated calls,
        # or using a generic local channel construct if Asterisk supports it for originating.
        # For dialing an internal extension like '7000' which is also a PJSIP endpoint,
        # originating FROM 'opendeep_trunk' TO '7000' is the goal.
        
        # The channel we want Asterisk to create and use for the outbound leg TO the target_phone_number.
        # This is usually just the technology and the number for external calls.
        # For internal calls to another PJSIP endpoint, it's PJSIP/extension_to_dial
        dial_string = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{target_phone_number}" # e.g., PJSIP/7000

        # CRITICAL: This is what executes your dialplan logic
        application_to_run = "AudioSocket" # Or you can use Dial or another app if you want,
                                          # but then that app needs to lead to AudioSocket

                   # Define variables for clarity
        call_attempt_id_var = f"_CALL_ATTEMPT_ID={self.call_id}"
        target_uri_var = f"_TARGET_AUDIOSOCKET_URI={full_audiosocket_uri_with_call_id}"
        actual_dial_var = f"_ACTUAL_TARGET_TO_DIAL={dial_string}"
           
           # Format the variables into a single pipe-separated string
        vars_to_pass = f"{self.call_id}|{dial_string}"
        dial_string_for_local_channel = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{target_phone_number}" # e.g. PJSIP/7000
        originate_action = AmiAction(
            "Originate",
            # We are NOT using a Local channel here for the initial Originate command.
            # We are telling Asterisk to create a channel and immediately
            # send it to our dialplan context.
            #Channel=f"Local/s@{app_config.DEFAULT_ASTERISK_CONTEXT}",
           # Channel=f"Local/s@opendeep-holding-context", # <-- NEW LINE
            Channel=f"Local/s@{app_config.DEFAULT_ASTERISK_CONTEXT}",
            Context="opendeep-audiosocket-outbound", # The context where our logic lives
            Exten="s",                               # The 'start' extension
            Priority=1,                              # The first step
            CallerID=f"OpenDeep <{app_config.DEFAULT_CALLER_ID_EXTEN}>",
            Timeout=30000,
            Async="true",
            # We pass our variables here. The dialplan will receive them.
            Variable=f"OPENDDEEP_VARS={vars_to_pass}"
        )
        
        self.originate_action_id = originate_action.get_action_id()
        logger.info(f"[CallAttemptHandler:{self.call_id}] Originate ActionID set to: {self.originate_action_id}")

        self.call_start_time = datetime.now()
        response = await self.ami_client.send_action(originate_action, timeout=1.0)
        
        if response and response.get("Response") == "Success":
            logger.info(f"[CallAttemptHandler:{self.call_id}] Originate command sent successfully to Asterisk for phone: {target_phone_number} (Task Phone: {original_number_for_logging}). ActionID: {self.originate_action_id}. Awaiting events.")
            await self._update_call_status_db(CallStatus.ORIGINATING)
            return True
        else:
            err_msg = response.get('Message', 'Unknown error') if response else "No response from AMI client"
            logger.error(f"[CallAttemptHandler:{self.call_id}] Failed to send Originate command to Asterisk. Response: {response}. Error: {err_msg}")
            await self._update_call_status_db(CallStatus.FAILED_ASTERISK_ERROR, hangup_cause=f"Originate failed: {err_msg}")
            return False

    async def _handle_redis_command(self, channel: str, command_data_dict: dict):
        logger.debug(f"[CallAttemptHandler:{self.call_id}] Received Redis command on {channel}: {command_data_dict}")
        command_type = command_data_dict.get("command_type")

        if not self.asterisk_channel_name:
            logger.warning(f"[CallAttemptHandler:{self.call_id}] Cannot process Redis command '{command_type}', Asterisk channel/ID is not yet known.")
            return

        if command_type == RedisDTMFCommand.model_fields['command_type'].default:
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
        
        elif command_type == RedisEndCallCommand.model_fields['command_type'].default:
            try:
                cmd = RedisEndCallCommand(**command_data_dict)
                logger.info(f"[CallAttemptHandler:{self.call_id}] Processing EndCall command for channel {self.asterisk_channel_name}. Reason: {cmd.reason}")
                hangup_action = AmiAction("Hangup", Channel=self.asterisk_channel_name, Cause="16")
                response = await self.ami_client.send_action(hangup_action)
                if response and response.get("Response") == "Success":
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Hangup command sent successfully for channel {self.asterisk_channel_name}.")
                else:
                    logger.error(f"[CallAttemptHandler:{self.call_id}] Failed to send Hangup command. Response: {response}")
            except Exception as e:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Error processing EndCall command: {e}", exc_info=True)
        else:
            logger.warning(f"[CallAttemptHandler:{self.call_id}] Unknown Redis command type: {command_type}")

    async def _listen_for_redis_commands(self):
        redis_channel_pattern = f"call_commands:{self.call_id}"
        logger.info(f"[CallAttemptHandler:{self.call_id}] Subscribing to Redis channel: {redis_channel_pattern}")
        try:
            await self.redis_client.subscribe_to_channel(redis_channel_pattern, self._handle_redis_command)
        except asyncio.CancelledError:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener task cancelled.")
        except Exception as e:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Critical error in Redis listener for {redis_channel_pattern}: {e}", exc_info=True)
            if not self.call_end_time:
                await self._handle_call_ended(
                    hangup_cause="Redis listener failure",
                    call_conclusion="Call terminated due to Redis communication error.",
                    final_status=CallStatus.FAILED_INTERNAL_ERROR
                )
        finally:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener stopped for channel {redis_channel_pattern}.")

    async def _process_ami_event(self, event: Dict[str, Any]):
        """Processes an AMI event. This is registered as a generic listener with ami_client."""
        if self._loop is None: self._loop = asyncio.get_running_loop()
        
        event_name = event.get("Event")
        unique_id = event.get("UniqueID")
        linked_id = event.get("Linkedid", unique_id)
        channel = event.get("Channel")
        action_id_in_event = event.get("ActionID")
        
        # --- Initial Correlation & UniqueID/Channel Discovery ---
        if not self.asterisk_unique_id:
            is_related_to_our_originate = (action_id_in_event and self.originate_action_id and action_id_in_event == self.originate_action_id)
            call_attempt_id_var = event.get("CALL_ATTEMPT_ID", event.get("call_attempt_id")) # Check for our custom variable
            is_our_call_attempt_var = (call_attempt_id_var == str(self.call_id))

            potential_discovery_events = ["Newchannel", "VarSet", "OriginateResponse", "Dial", "DialBegin", "DialState", "BridgeEnter"]
            if unique_id and event_name in potential_discovery_events:
                if is_related_to_our_originate or is_our_call_attempt_var:
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Captured Asterisk UniqueID: {unique_id} and Channel: {channel} from event {event_name} linked to our call attempt.")
                    self.asterisk_unique_id = unique_id
                    if channel: self.asterisk_channel_name = channel
                    
                    await self._update_call_status_db(self.call_record.status, # Keep current status
                                                      asterisk_channel=self.asterisk_channel_name,
                                                      call_uuid=self.asterisk_unique_id)
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Our Call UniqueID is now: {self.asterisk_unique_id}, Channel: {self.asterisk_channel_name}")
            
            elif is_related_to_our_originate:
                logger.debug(f"[CallAttemptHandler:{self.call_id}] Received event {event_name} related to our Originate ActionID {self.originate_action_id}, but no UniqueID yet. Event: {event}")
                return
            else: # Event not yet correlated
                return

        # --- Filtering for known UniqueID ---
        if self.asterisk_unique_id:
            relevant_to_us = False
            if unique_id == self.asterisk_unique_id: relevant_to_us = True
            elif linked_id == self.asterisk_unique_id: relevant_to_us = True
            elif channel and self.asterisk_channel_name and channel.startswith(self.asterisk_channel_name.split('-')[0]):
                 relevant_to_us = True

            if not relevant_to_us:
                # logger.debug(f"[CallAttemptHandler:{self.call_id}] Ignoring event for different UniqueID/Channel: EventUID='{unique_id}', EventChan='{channel}' vs OurUID='{self.asterisk_unique_id}', OurChan='{self.asterisk_channel_name}'")
                return
        elif not self.originate_action_id: # Should not happen if _originate_call was successful
            logger.warning(f"[CallAttemptHandler:{self.call_id}] No Asterisk UniqueID and no Originate ActionID known. Cannot process event: {event_name}")
            return


        logger.info(f"[CallAttemptHandler:{self.call_id}] Processing relevant AMI Event: {event_name}, UniqueID: {unique_id}, LinkedID: {linked_id}, Channel: {channel}, ActionID: {action_id_in_event}")

        # --- Specific Event Handling (Restored fuller structure) ---
        if event_name == "Newchannel":
            if unique_id == self.asterisk_unique_id: # Make sure this Newchannel event is for OUR channel
                if not self.asterisk_channel_name and channel:
                    self.asterisk_channel_name = channel
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Newchannel. Updated Channel: {channel} for UniqueID: {unique_id}")
                    await self._loop.run_in_executor(None, db_manager.update_call_status, self.call_id, self.call_record.status, None, None, None, channel, None)
                current_status = self.call_record.status
                if current_status not in [CallStatus.DIALING, CallStatus.RINGING, CallStatus.ANSWERED]:
                    await self._update_call_status_db(CallStatus.DIALING)

        elif event_name == "VarSet":
            # Check if this VarSet is for our CALL_ATTEMPT_ID variable
            if event.get("Variable", "").upper() == "CALL_ATTEMPT_ID" and event.get("Value") == str(self.call_id):
                if not self.asterisk_unique_id and unique_id: # If we haven't captured UniqueID yet
                    self.asterisk_unique_id = unique_id
                    if channel and not self.asterisk_channel_name: self.asterisk_channel_name = channel
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Captured Asterisk UniqueID via VarSet for CALL_ATTEMPT_ID: {unique_id}, Channel: {channel}")
                    await self._update_call_status_db(self.call_record.status, asterisk_channel=channel, call_uuid=unique_id)
            # Other VarSet events can be logged or handled if needed
            # logger.debug(f"[CallAttemptHandler:{self.call_id}] VarSet event: {event}")


        elif event_name == "Dial": # Dial event family (DialBegin, DialState, DialEnd)
            # This event is complex and can have SubEvents like Begin, End, State.
            # The UniqueID in a Dial event usually refers to the originating channel.
            # The DestUniqueID refers to the channel being dialed.
            sub_event = event.get("SubEvent")
            dial_status = event.get("DialStatus") # Present in DialEnd
            dest_unique_id = event.get("DestUniqueID")

            if sub_event == "Begin":
                logger.info(f"[CallAttemptHandler:{self.call_id}] Dial Begin. Channel: {event.get('Channel')}, DestChannel: {event.get('DestChannel')}, DestUniqueID: {dest_unique_id}")
                # Potentially update status to ringing if not already
                if self.call_record.status not in [CallStatus.RINGING, CallStatus.ANSWERED]: # If not already ringing or answered
                     await self._update_call_status_db(CallStatus.RINGING)
            
            elif sub_event == "End":
                logger.info(f"[CallAttemptHandler:{self.call_id}] Dial End. Channel: {event.get('Channel')}, DestChannel: {event.get('DestChannel')}, DialStatus: {dial_status}")
                if dial_status == "ANSWER":
                    if not self.call_answer_time: self.call_answer_time = datetime.now()
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Call Answered (DialEnd:ANSWER).")
                    await self._update_call_status_db(CallStatus.ANSWERED)
                    # AudioSocketHandler will update to LIVE_AI_HANDLING later
                elif dial_status in ["NOANSWER", "CANCEL", "DONTCALL", "TORTURE"]: # Added more failure statuses
                    await self._handle_call_ended(hangup_cause=f"DialEnd: {dial_status}", call_conclusion="No effective answer", final_status=CallStatus.FAILED_NO_ANSWER)
                elif dial_status == "BUSY":
                    await self._handle_call_ended(hangup_cause="DialEnd: BUSY", call_conclusion="Line busy", final_status=CallStatus.FAILED_BUSY)
                elif dial_status == "CONGESTION":
                     await self._handle_call_ended(hangup_cause="DialEnd: CONGESTION", call_conclusion="Network congestion", final_status=CallStatus.FAILED_CONGESTION)
                elif dial_status in ["CHANUNAVAIL", "INVALIDARGS"]: # Channel unavailable or other setup issue
                     await self._handle_call_ended(hangup_cause=f"DialEnd: {dial_status}", call_conclusion="Channel/config issue", final_status=CallStatus.FAILED_CHANNEL_UNAVAILABLE)
            
            # elif sub_event == "State": # Can provide intermediate state like "Ring", "Ringing"
                # dial_state = event.get("DialState")
                # logger.debug(f"[CallAttemptHandler:{self.call_id}] Dial State: {dial_state} for DestChannel: {event.get('DestChannel')}")
                # if dial_state in ["RING", "RINGING"] and self.call_record.status != CallStatus.RINGING:
                    # await self._update_call_status_db(CallStatus.RINGING)


        elif event_name == "Hangup":
            hangup_cause_code = event.get("Cause", "0") 
            hangup_cause_txt = event.get("Cause-txt", f"Unknown (Code: {hangup_cause_code})")
            logger.info(f"[CallAttemptHandler:{self.call_id}] Hangup event for UniqueID {unique_id}. Cause: {hangup_cause_txt} (Code: {hangup_cause_code})")
            
            final_status = CallStatus.COMPLETED_SYSTEM_HANGUP 
            if hangup_cause_code == "16": # Normal Clearing
                call_db_record = await self._loop.run_in_executor(None, db_manager.get_call_by_id, self.call_id)
                if call_db_record and call_db_record.status in [CallStatus.COMPLETED_AI_OBJECTIVE_MET, CallStatus.COMPLETED_AI_HANGUP]:
                    final_status = call_db_record.status
                else:
                    final_status = CallStatus.COMPLETED_USER_HANGUP 
            elif hangup_cause_code == "17": final_status = CallStatus.FAILED_BUSY
            elif hangup_cause_code == "1": final_status = CallStatus.FAILED_INVALID_NUMBER
            # Add more cause code mappings as needed
            
            await self._handle_call_ended(hangup_cause=f"{hangup_cause_txt} (Code: {hangup_cause_code})",
                                          call_conclusion=f"Call ended by Hangup event: {hangup_cause_txt}",
                                          final_status=final_status)
        
        elif event_name == "OriginateResponse": # If the AMI library emits this as a discrete event
            # This event directly responds to an Originate action.
            # It should contain the ActionID of the Originate request.
            # It might also provide UniqueID and Channel if the origination is proceeding.
            logger.info(f"[CallAttemptHandler:{self.call_id}] Received OriginateResponse: {event}")
            if event.get("Response") == "Failure":
                logger.error(f"[CallAttemptHandler:{self.call_id}] OriginateResponse indicates failure: {event.get('Reason')}")
                await self._handle_call_ended(
                    hangup_cause=f"OriginateResponse Failure: {event.get('Reason', 'Unknown')}",
                    call_conclusion="Origination failed as per Asterisk response.",
                    final_status=CallStatus.FAILED_ASTERISK_ERROR
                )
            # If success, UniqueID/Channel might be here or will come in Newchannel.

        elif event_name == "BridgeEnter":
            # Indicates a channel has entered a bridge. Useful for tracking call progress.
            logger.info(f"[CallAttemptHandler:{self.call_id}] BridgeEnter event: Channel {channel} (UniqueID {unique_id}) entered bridge {event.get('BridgeUniqueid')}")
            # If this is our main channel and it enters a bridge with the dialed party, call is truly connected.
            # Status should likely already be ANSWERED from Dial event.

        elif event_name == "BridgeLeave":
            logger.info(f"[CallAttemptHandler:{self.call_id}] BridgeLeave event: Channel {channel} (UniqueID {unique_id}) left bridge {event.get('BridgeUniqueid')}")

        # --- Placeholder for other events if needed ---
        # elif event_name == "SomeOtherEvent":
        #     logger.debug(f"[CallAttemptHandler:{self.call_id}] Received SomeOtherEvent: {event}")

    async def _handle_call_ended(self, hangup_cause: str, call_conclusion: str, final_status: CallStatus):
        if self._loop is None: self._loop = asyncio.get_running_loop()
        if self.call_end_time:
            logger.warning(f"[CallAttemptHandler:{self.call_id}] Call end already processed for UID {self.asterisk_unique_id}.")
            return

        self.call_end_time = datetime.now()
        duration_seconds = None
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

        if self.unregister_callback:
            await self.unregister_callback(self.call_id)
        
        logger.info(f"[CallAttemptHandler:{self.call_id}] Processing finished for UID {self.asterisk_unique_id}.")

    async def _update_call_status_db(self, status: CallStatus, **kwargs):
        if self._loop is None: self._loop = asyncio.get_running_loop()
        self.call_record.status = status
        # db_manager.update_call_status is synchronous, run in executor
        # Prepare arguments for the synchronous function
        sync_kwargs = {
            'hangup_cause': kwargs.get("hangup_cause"),
            'call_conclusion': kwargs.get("call_conclusion"),
            'duration_seconds': kwargs.get("duration_seconds"),
            'asterisk_channel': kwargs.get("asterisk_channel"),
            'call_uuid': kwargs.get("call_uuid")
        }
        await self._loop.run_in_executor(
            None, 
            db_manager.update_call_status, 
            self.call_id, 
            status,
            # Pass individual args expected by db_manager.update_call_status
            sync_kwargs['hangup_cause'],
            sync_kwargs['call_conclusion'],
            sync_kwargs['duration_seconds'],
            sync_kwargs['asterisk_channel'],
            sync_kwargs['call_uuid']
        )

    # In call_processor_service/call_attempt_handler.py

    async def manage_call_lifecycle(self):
        self._loop = asyncio.get_running_loop()
        logger.info(f"[CallAttemptHandler:{self.call_id}] Starting to manage call lifecycle.")

        # <<< START: MODIFIED LOGIC >>>
        # 1. Start listening for AMI events BEFORE originating the call.
        self.ami_client.add_generic_event_listener(self._process_ami_event)
        self._ami_event_listener_task_active = True
        logger.info(f"[CallAttemptHandler:{self.call_id}] Added generic AMI event listener.")

        # 2. Start listening for Redis commands.
        self._redis_listener_task = asyncio.create_task(self._listen_for_redis_commands())
        
        # 3. NOW, originate the call.
        origination_success = await self._originate_call()
        if not origination_success:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Origination failed. Aborting lifecycle management.")
            # The failure is already handled inside _originate_call, but we must unregister the handler here.
            if self.unregister_callback:
                await self.unregister_callback(self.call_id)
            # The 'finally' block will handle cleanup of listeners.
            return 
        # <<< END: MODIFIED LOGIC >>>

        try:
            # 4. Wait for the call to end (via stop_event set by an event handler).
            await self._stop_event.wait()
            logger.info(f"[CallAttemptHandler:{self.call_id}] Stop event received. Proceeding to cleanup.")
        except asyncio.CancelledError:
            logger.info(f"[CallAttemptHandler:{self.call_id}] Main lifecycle task cancelled.")
            if not self.call_end_time:
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
            
            if self._redis_listener_task and not self._redis_listener_task.done():
                logger.info(f"[CallAttemptHandler:{self.call_id}] Cancelling Redis listener task.")
                self._redis_listener_task.cancel()
                try: await self._redis_listener_task
                except asyncio.CancelledError: logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener task successfully cancelled during cleanup.")
                except Exception as e_redis_cancel: logger.error(f"[CallAttemptHandler:{self.call_id}] Error awaiting cancelled Redis listener: {e_redis_cancel}")
            
            if self._ami_event_listener_task_active:
                self.ami_client.remove_generic_event_listener(self._process_ami_event)
                self._ami_event_listener_task_active = False
                logger.info(f"[CallAttemptHandler:{self.call_id}] Removed generic AMI event listener during cleanup.")

            # This check is important. If the call never properly ended (e.g. error before _handle_call_ended was called),
            # we need to make sure it's unregistered.
            if not self.call_end_time:
                if self.unregister_callback:
                    logger.warning(f"[CallAttemptHandler:{self.call_id}] Call end not processed. Ensuring unregistration.")
                    await self.unregister_callback(self.call_id)
            
            logger.info(f"[CallAttemptHandler:{self.call_id}] Lifecycle management fully ended for Call ID {self.call_id}.")