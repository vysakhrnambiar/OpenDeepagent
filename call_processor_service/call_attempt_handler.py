# call_processor_service/call_attempt_handler.py

import asyncio
import uuid
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
from common.data_models import RedisDTMFCommand, RedisEndCallCommand, RedisAIHandshakeCommand
from call_processor_service.asterisk_ami_client import AsteriskAmiClient, AmiAction
from audio_processing_service.openai_realtime_client import OpenAIRealtimeClient

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
        self.outbound_channel_name: Optional[str] = None # To store the PJSIP channel for DTMF
        self.originate_action_id: Optional[str] = None
        
        self.call_start_time: Optional[datetime] = None
        self.call_answer_time: Optional[datetime] = None
        self.call_end_time: Optional[datetime] = None

        self.asterisk_call_specific_uuid: Optional[str] = None # Will hold the UUID for AudioSocket path
        self.openai_client: Optional[OpenAIRealtimeClient] = None # Store OpenAI client instance
        self.openai_session_id: Optional[str] = None # Store OpenAI session ID

        self._stop_event = asyncio.Event()
        self._redis_listener_task: Optional[asyncio.Task] = None
        self._ami_event_listener_task_active = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None # For run_in_executor
        self._channel_identified_event = asyncio.Event()
 
        logger.info(f"[CallAttemptHandler:{self.call_id}] Initialized for Task ID: {self.task_id}, User ID: {self.task_user_id}")
 
    async def _originate_call(self) -> bool:
        logger.info(f"[CallAttemptHandler:{self.call_id}] Preparing to originate call.")
        if self._loop is None: self._loop = asyncio.get_running_loop()
        
        # 1. Get task details
        task = await self._loop.run_in_executor(None, db_manager.get_task_by_id, self.task_id)
        if not task:
            logger.error(f"[CallAttemptHandler:{self.call_id}] Could not fetch task details for Task ID {self.task_id}. Aborting.")
            await self._update_call_status_db(CallStatus.FAILED_INTERNAL_ERROR, hangup_cause="Task details unavailable")
            return False

        # Generate UUID for this call
        self.asterisk_call_specific_uuid = str(uuid.uuid4())
        logger.info(f"[CallAttemptHandler:{self.call_id}] Generated UUID for AudioSocket: {self.asterisk_call_specific_uuid}")

        # 3. Proceed with call setup
        target_phone_number = task.phone_number
        original_number_for_logging = task.phone_number

        if app_config.APP_TEST_MODE:
            target_phone_number = app_config.APP_TEST_MODE_REDIRECT_NUMBER
            logger.warning(f"[CallAttemptHandler:{self.call_id}] TEST MODE ENABLED. Redirecting call for {original_number_for_logging} to {target_phone_number}.")

        channel_to_dial = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{app_config.DEFAULT_CALLER_ID_EXTEN}/{target_phone_number}"
        
        # Generate UUID and immediately update database
        self.asterisk_call_specific_uuid = str(uuid.uuid4())
        logger.info(f"[CallAttemptHandler:{self.call_id}] Generated Asterisk-specific UUID for AudioSocket: {self.asterisk_call_specific_uuid}")
        
        # Update database with UUID first to prevent race condition
        await self._update_call_status_db(CallStatus.PENDING_ORIGINATION, call_uuid=self.asterisk_call_specific_uuid)
        
        # Now set up the AudioSocket URI
        full_audiosocket_uri_with_uuid = f"ws://{app_config.AUDIOSOCKET_HOST}:{app_config.AUDIOSOCKET_PORT}/callaudio/{self.asterisk_call_specific_uuid}"
        logger.info(f"[CallAttemptHandler:{self.call_id}] AudioSocket URI will be: {full_audiosocket_uri_with_uuid}")
        
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
        dial_string_for_dialplan = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{target_phone_number}" 
        # CRITICAL: This is what executes your dialplan logic
        application_to_run = "AudioSocket" # Or you can use Dial or another app if you want,
                                          # but then that app needs to lead to AudioSocket


        
        # Pass UUID, dial string, and OpenAI session ID to dialplan
        vars_to_pass = f"{self.asterisk_call_specific_uuid}|{dial_string_for_dialplan}"
        logger.info(f"[CallAttemptHandler:{self.call_id}] Passing to dialplan: UUID={self.asterisk_call_specific_uuid}")


                   # Define variables for clarity
        #call_attempt_id_var = f"_CALL_ATTEMPT_ID={self.call_id}"
        #target_uri_var = f"_TARGET_AUDIOSOCKET_URI={full_audiosocket_uri_with_call_id}"
        #actual_dial_var = f"_ACTUAL_TARGET_TO_DIAL={dial_string}"
           
           # Format the variables into a single pipe-separated string
        #vars_to_pass = f"{self.call_id}|{dial_string}"
        #dial_string_for_local_channel = f"{app_config.DEFAULT_ASTERISK_CHANNEL_TYPE}/{target_phone_number}" # e.g. PJSIP/7000
        originate_action = AmiAction(
            "Originate",
            # We are NOT using a Local channel here for the initial Originate command.
            # We are telling Asterisk to create a channel and immediately
            # send it to our dialplan context.
            #Channel=f"Local/s@{app_config.DEFAULT_ASTERISK_CONTEXT}",
           # Channel=f"Local/s@opendeep-holding-context", # <-- NEW LINE
            #Channel=f"Local/s@{app_config.DEFAULT_ASTERISK_CONTEXT}",
            #Channel=f"Local/s@opendeep-ai-leg",
            Channel=f"Local/s@test-audiosocket-playback-first",
            #Context="opendeep-audiosocket-outbound", # The context where our logic lives
            Context="opendeep-human-leg",  
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
        response = await self.ami_client.send_action(
            originate_action,
            timeout=1.0,
            event_callback=self._process_ami_event
        )
        
        if response and response.get("Response") == "Success":
            logger.info(f"[CallAttemptHandler:{self.call_id}] Originate command sent successfully to Asterisk for phone: {target_phone_number}. ActionID: {self.originate_action_id}. Awaiting events via action-specific callback.")
            # Update only the status - UUID is already in database
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

        if command_type == "send_dtmf":
            # --- NEW DTMF LOGIC ---
            if not self.outbound_channel_name:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Cannot send DTMF. Outbound channel has not been identified yet.")
                return
            try:
                cmd = RedisDTMFCommand(**command_data_dict)
                logger.info(f"[CallAttemptHandler:{self.call_id}] Processing DTMF command: sending '{cmd.digits}' to outbound channel {self.outbound_channel_name}")
                for digit in cmd.digits:
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Sending DTMF digit: '{digit}' to channel {self.outbound_channel_name}")
                    dtmf_action = AmiAction("PlayDTMF", Channel=self.outbound_channel_name, Digit=digit)
                    response = await self.ami_client.send_action(dtmf_action, timeout=3.0)
                    if response and response.get("Response") == "Success":
                        logger.info(f"[CallAttemptHandler:{self.call_id}] DTMF digit '{digit}' sent successfully.")
                    else:
                        logger.error(f"[CallAttemptHandler:{self.call_id}] Failed to send DTMF digit '{digit}'. Response: {response}")
                        break
                    await asyncio.sleep(0.25)
            except Exception as e:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Error processing DTMF command: {e}", exc_info=True)

        elif command_type == "end_call":
            # --- HANGUP LOGIC (Uses original channel) ---
            if not self.asterisk_channel_name:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Cannot process EndCall command, Asterisk channel could not be identified.")
                return
            try:
                cmd = RedisEndCallCommand(**command_data_dict)
                
                # --- DYNAMIC DELAY LOGIC ---
                if cmd.final_message:
                    # Estimate delay based on message length. Avg speaking rate is ~150 WPM.
                    # 150 words / 60s = 2.5 words/sec. So, 1 word takes ~0.4s.
                    # We add a small buffer.
                    word_count = len(cmd.final_message.split())
                    estimated_speech_duration_s = (word_count * 0.4) + 0.5 # 400ms per word + 500ms buffer
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Received final_message with {word_count} words. Waiting for {estimated_speech_duration_s:.2f}s before hangup.")
                    await asyncio.sleep(estimated_speech_duration_s)
                else:
                    logger.warning(f"[CallAttemptHandler:{self.call_id}] No final_message in EndCall command. Hanging up immediately.")
                # --- END DYNAMIC DELAY LOGIC ---

                logger.info(f"[CallAttemptHandler:{self.call_id}] Processing EndCall command for channel {self.asterisk_channel_name}. Reason: {cmd.reason}")
                hangup_action = AmiAction("Hangup", Channel=self.asterisk_channel_name, Cause="16")
                response = await self.ami_client.send_action(hangup_action)
                if response and response.get("Response") == "Success":
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Hangup command sent successfully for channel {self.asterisk_channel_name}.")
                    self._stop_event.set() # Trigger the lifecycle to end now that hangup is sent.
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
        if self._loop is None: self._loop = asyncio.get_running_loop()

        event_name = event.get("Event")
        action_id_in_event = event.get("ActionID")
        unique_id_from_event = event.get("Uniqueid")
        linked_id_from_event = event.get("Linkedid")
        channel_from_event = event.get("Channel")

        # --- Phase 1: Initial Discovery of self.asterisk_unique_id (Asterisk's internal channel ID) ---
        if not self.asterisk_unique_id: # If we haven't identified the main Asterisk UniqueID for this call yet
            is_related_to_our_originate = (action_id_in_event and 
                                           self.originate_action_id and 
                                           action_id_in_event == self.originate_action_id)

            # Mechanism 1: ActionID matching (primary for early events like OriginateResponse, Newchannel for Local/...)
            if is_related_to_our_originate and unique_id_from_event and event_name in ["Newchannel", "OriginateResponse", "Dial", "DialBegin"]: # Added Dial/DialBegin
                self.asterisk_unique_id = unique_id_from_event
                if channel_from_event: self.asterisk_channel_name = channel_from_event
                logger.info(f"[CallAttemptHandler:{self.call_id}] Discovered Asterisk internal UniqueID: {self.asterisk_unique_id} via ActionID match on event {event_name}. Channel: {self.asterisk_channel_name}")
                if self.asterisk_channel_name: self._channel_identified_event.set()
                # Update DB status or channel info. Storing the channel is useful.
                # The call_uuid passed here is self.asterisk_call_specific_uuid (our generated one for audiosocket path)
                # which should have been saved to DB right after sending Originate.
                await self._update_call_status_db(self.call_record.status, asterisk_channel=self.asterisk_channel_name, call_uuid=self.asterisk_call_specific_uuid)
                # No return here, fall through to Phase 2 to process THIS event too.

            # Mechanism 2: Custom Channel Variable matching from VarSet event
            elif event_name == "VarSet":
                variable_name_from_event = event.get("Variable")
                value_from_event = event.get("Value")
                
                # We need to identify the call by the UUID we generated.
                # The variable name from the dialplan could be one of two possibilities based on recent changes.
                # Let's check for both to be robust.
                var_name_upper = variable_name_from_event.upper() if variable_name_from_event else ""
                
                # Check 1: The variable is `OPENDDEEP_VARS` and our UUID is *in* its value.
                # This is set by the Originate command itself.
                is_match_on_originate_vars = (var_name_upper == "OPENDDEEP_VARS" and
                                              value_from_event and
                                              self.asterisk_call_specific_uuid in value_from_event)

                # Check 2: The variable is `_OPENDDEEP_CALL_UUID` and its value *is* our UUID.
                # This is likely set by the dialplan logic itself.
                is_match_on_dialplan_var = (var_name_upper == "_OPENDDEEP_CALL_UUID" and
                                            value_from_event == self.asterisk_call_specific_uuid)

                if (is_match_on_originate_vars or is_match_on_dialplan_var) and unique_id_from_event:
                    
                    self.asterisk_unique_id = unique_id_from_event
                    if channel_from_event: self.asterisk_channel_name = channel_from_event
                    
                    # Log which variable we matched on for easier debugging
                    matched_var_name = "OPENDDEEP_VARS" if is_match_on_originate_vars else "_OPENDDEEP_CALL_UUID"
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Discovered Asterisk internal UniqueID: {self.asterisk_unique_id} via VarSet for {matched_var_name}. Channel: {self.asterisk_channel_name}")
                    
                    if self.asterisk_channel_name: self._channel_identified_event.set()
                    await self._update_call_status_db(self.call_record.status, asterisk_channel=self.asterisk_channel_name, call_uuid=self.asterisk_call_specific_uuid)
                    # No return here, fall through to Phase 2.
            
            # Handle OriginateResponse Failure specifically if it's for our ActionID
            elif is_related_to_our_originate and event_name == "OriginateResponse" and event.get("Response") == "Failure":
                reason = event.get('Reason', 'Unknown reason')
                logger.error(f"[CallAttemptHandler:{self.call_id}] OriginateResponse indicates failure (ActionID: {action_id_in_event}): {reason}")
                await self._handle_call_ended(
                    hangup_cause=f"OriginateResponse Failure: {reason}",
                    call_conclusion="Origination failed at Asterisk AMI level.",
                    final_status=CallStatus.FAILED_ASTERISK_ERROR
                )
                return # Call definitely ended or failed to start.

            else: # Event is not yet correlated or doesn't provide the UniqueID.
                logger.debug(f"[CallAttemptHandler:{self.call_id}] Event {event_name} (ActionID: {action_id_in_event}, UID: {unique_id_from_event}) not yet correlated or not providing initial Asterisk UniqueID.")
                return

        # --- Phase 2: Processing events for an already identified call ---
        # If self.asterisk_unique_id is still None here, it means initial discovery failed for this event.
        if not self.asterisk_unique_id:
            logger.debug(f"[CallAttemptHandler:{self.call_id}] No Asterisk UniqueID established for event {event_name}. Ignoring.")
            return

        # Check if the current event is relevant to our call using the established self.asterisk_unique_id
        is_relevant = False
        if unique_id_from_event == self.asterisk_unique_id: is_relevant = True
        elif linked_id_from_event == self.asterisk_unique_id: is_relevant = True
        # A more robust channel name check might be needed if channel names are very dynamic
        # For now, UniqueID and LinkedID are primary.

        if not is_relevant:
            # logger.debug(f"[CallAttemptHandler:{self.call_id}] Event {event_name} (UID: {unique_id_from_event}, LinkedID: {linked_id_from_event}) not relevant to our call (OurAstUID: {self.asterisk_unique_id}).")
            return

        # If relevant, proceed with specific event handling:
        logger.info(f"[CallAttemptHandler:{self.call_id}] Processing relevant AMI Event: {event_name} for our call (OurAstUID: {self.asterisk_unique_id}) EventDetails: { {k:v for k,v in event.items() if k not in ['Privilege']} }")

        if event_name == "Newchannel":
            # This might be for a secondary channel leg (e.g., the one actually dialing out after Local channel)
            # If this new channel's UniqueID IS our self.asterisk_unique_id, it means we've just identified it.
            # If it's a *different* UniqueID but *Linked* to ours, it's also relevant.
            # The self.asterisk_channel_name should ideally be the primary channel we are tracking.
            if not self.asterisk_channel_name and channel_from_event and unique_id_from_event == self.asterisk_unique_id:
                self.asterisk_channel_name = channel_from_event
                logger.info(f"[CallAttemptHandler:{self.call_id}] Updated/Confirmed Channel: {self.asterisk_channel_name} for our main Asterisk UniqueID: {self.asterisk_unique_id}")
                await self._update_call_status_db(self.call_record.status, asterisk_channel=self.asterisk_channel_name)
            
            current_db_status = self.call_record.status # Use the live status from self.call_record
            if current_db_status not in [CallStatus.DIALING, CallStatus.RINGING, CallStatus.ANSWERED, CallStatus.LIVE_AI_HANDLING]:
                await self._update_call_status_db(CallStatus.DIALING)

        elif event_name == "DialBegin":
            dest_channel = event.get("DestChannel")
            logger.info(f"[CallAttemptHandler:{self.call_id}] Dial Begin to DestChannel: {dest_channel} (DestUID: {event.get('DestUniqueID')})")
            if dest_channel:
                logger.info(f"[CallAttemptHandler:{self.call_id}] Captured outbound channel for DTMF: '{dest_channel}'")
                self.outbound_channel_name = dest_channel
            if self.call_record.status not in [CallStatus.RINGING, CallStatus.ANSWERED, CallStatus.LIVE_AI_HANDLING]:
                await self._update_call_status_db(CallStatus.RINGING)

        elif event_name == "DialEnd":
            dial_status_from_event = event.get("DialStatus")
            logger.info(f"[CallAttemptHandler:{self.call_id}] Dial End. DestChannel: {event.get('DestChannel')}, DialStatus: {dial_status_from_event}")
            if dial_status_from_event == "ANSWER":
                if not self.call_answer_time:
                    self.call_answer_time = datetime.now()
                    logger.info(f"[CallAttemptHandler:{self.call_id}] Call Answered (DialEnd:ANSWER).")
                    await self._update_call_status_db(CallStatus.ANSWERED)
            elif dial_status_from_event in ["NOANSWER", "CANCEL", "DONTCALL", "TORTURE"]:
                await self._handle_call_ended(hangup_cause=f"DialEnd: {dial_status_from_event}", call_conclusion="No effective answer", final_status=CallStatus.FAILED_NO_ANSWER)
            elif dial_status_from_event == "BUSY":
                await self._handle_call_ended(hangup_cause="DialEnd: BUSY", call_conclusion="Line busy", final_status=CallStatus.FAILED_BUSY)
            elif dial_status_from_event == "CONGESTION":
                await self._handle_call_ended(hangup_cause="DialEnd: CONGESTION", call_conclusion="Network congestion", final_status=CallStatus.FAILED_CONGESTION)
            elif dial_status_from_event in ["CHANUNAVAIL", "INVALIDARGS"]:
                await self._handle_call_ended(hangup_cause=f"DialEnd: {dial_status_from_event}", call_conclusion="Channel/config issue", final_status=CallStatus.FAILED_CHANNEL_UNAVAILABLE)

        elif event_name == "Hangup":
            if self.call_end_time: # Already handled
                return
            hangup_cause_code = event.get("Cause", "0")
            hangup_cause_txt = event.get("Cause-txt", f"Unknown (Code: {hangup_cause_code})")
            logger.info(f"[CallAttemptHandler:{self.call_id}] Hangup event for UniqueID {unique_id_from_event}. Cause: {hangup_cause_txt} (Code: {hangup_cause_code})")
            
            final_status = CallStatus.COMPLETED_SYSTEM_HANGUP
            if hangup_cause_code == "16": # Normal Clearing
                call_db_record = await self._loop.run_in_executor(None, db_manager.get_call_by_id, self.call_id)
                if call_db_record:
                    if call_db_record.status in [CallStatus.COMPLETED_AI_OBJECTIVE_MET, CallStatus.COMPLETED_AI_HANGUP]:
                        final_status = call_db_record.status
                    else: # If AI hasn't marked it as completed, assume user hangup or system cleanup
                        final_status = CallStatus.COMPLETED_USER_HANGUP
            elif hangup_cause_code == "17": final_status = CallStatus.FAILED_BUSY
            elif hangup_cause_code == "1": final_status = CallStatus.FAILED_INVALID_NUMBER
            
            await self._handle_call_ended(
                hangup_cause=f"{hangup_cause_txt} (Code: {hangup_cause_code})",
                call_conclusion=f"Call ended by Hangup event: {hangup_cause_txt}",
                final_status=final_status
            )

        elif event_name == "BridgeEnter":
            bridge_unique_id = event.get("BridgeUniqueid")
            logger.info(f"[CallAttemptHandler:{self.call_id}] BridgeEnter event: Channel {channel_from_event} (OurAstUID: {self.asterisk_unique_id}) entered bridge {bridge_unique_id}. Type: {event.get('BridgeType')}")
            
            # --- NEW LOGIC TO CAPTURE OUTBOUND CHANNEL ---
            # The is_relevant check has already confirmed this event is linked to our call.
            # If the channel in the event is not our initial channel, it MUST be the outbound one.
            if channel_from_event and self.asterisk_channel_name and channel_from_event != self.asterisk_channel_name:
                logger.info(f"[CallAttemptHandler:{self.call_id}] BridgeEnter event from a different channel: '{channel_from_event}'. Capturing as outbound channel for DTMF.")
                self.outbound_channel_name = channel_from_event
            # --- END NEW LOGIC ---

            if not self.call_answer_time:
                self.call_answer_time = datetime.now()
                logger.info(f"[CallAttemptHandler:{self.call_id}] Call considered Answered (BridgeEnter). Publishing AI Handshake command.")
                await self._update_call_status_db(CallStatus.ANSWERED)
                if self.asterisk_call_specific_uuid:
                    handshake_command = RedisAIHandshakeCommand(asterisk_call_uuid=self.asterisk_call_specific_uuid)
                    channel = f"audiosocket_server_commands:{self.asterisk_call_specific_uuid}"
                    await self.redis_client.publish_command(channel, handshake_command.model_dump())
                else:
                    logger.error(f"[CallAttemptHandler:{self.call_id}] Cannot publish AI Handshake on BridgeEnter, asterisk_call_specific_uuid is not set.")

        # Other events like BridgeLeave, etc., can be added as needed.

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

        # --- NEW: Register a generic event listener for this handler ---
        self.ami_client.add_generic_event_listener(self._process_ami_event)
        logger.info(f"[CallAttemptHandler:{self.call_id}] Registered generic AMI event listener.")

        try:
            # 1. Start listening for Redis commands.
            self._redis_listener_task = asyncio.create_task(self._listen_for_redis_commands())
            
            # 2. Originate the call. The action-specific callback remains for rapid OriginateResponse handling.
            origination_success = await self._originate_call()
            if not origination_success:
                logger.error(f"[CallAttemptHandler:{self.call_id}] Origination failed. Aborting lifecycle management.")
                if self.unregister_callback:
                    await self.unregister_callback(self.call_id)
                return

            # 3. Wait for the call to end (via stop_event set by an event handler).
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
            
            # --- NEW: Unregister the generic event listener ---
            self.ami_client.remove_generic_event_listener(self._process_ami_event)
            logger.info(f"[CallAttemptHandler:{self.call_id}] Unregistered generic AMI event listener.")

            logger.info(f"[CallAttemptHandler:{self.call_id}] Final cleanup initiated.")
            
            if self._redis_listener_task and not self._redis_listener_task.done():
                logger.info(f"[CallAttemptHandler:{self.call_id}] Cancelling Redis listener task.")
                self._redis_listener_task.cancel()
                try: await self._redis_listener_task
                except asyncio.CancelledError: logger.info(f"[CallAttemptHandler:{self.call_id}] Redis listener task successfully cancelled during cleanup.")
                except Exception as e_redis_cancel: logger.error(f"[CallAttemptHandler:{self.call_id}] Error awaiting cancelled Redis listener: {e_redis_cancel}")
            
            # This check is important. If the call never properly ended (e.g. error before _handle_call_ended was called),
            # we need to make sure it's unregistered.
            if not self.call_end_time:
                if self.unregister_callback:
                    logger.warning(f"[CallAttemptHandler:{self.call_id}] Call end not processed. Ensuring unregistration.")
                    await self.unregister_callback(self.call_id)
            
            logger.info(f"[CallAttemptHandler:{self.call_id}] Lifecycle management fully ended for Call ID {self.call_id}.")