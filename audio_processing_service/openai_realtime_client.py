# audio_processing_service/openai_realtime_client.py
import asyncio
import json
import base64
import websockets.client
import websockets.exceptions
import time
from typing import Optional, AsyncGenerator
import sys # ADD THIS
from pathlib import Path # ADD THIS

# --- TEMPORARY PATH HACK FOR STANDALONE TESTING ---
# This should be active when running this file directly.
if __name__ == '__main__' or not __package__: # Checks if run as script or if package context is missing
    _project_root_for_standalone = Path(__file__).resolve().parent.parent
    if str(_project_root_for_standalone) not in sys.path:
        sys.path.insert(0, str(_project_root_for_standalone))
        print(f"DEBUG: Added to sys.path for standalone execution: {_project_root_for_standalone}")
# --- END TEMPORARY PATH HACK ---

# Assuming project structure allows this import
from config.app_config import app_config # For OPENAI_REALTIME_MODEL, OPENAI_CONNECT_RETRIES etc.
from common.logger_setup import setup_logger
from common.redis_client import RedisClient

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class OpenAIRealtimeClient:
    def __init__(self,
                 call_specific_prompt: str,
                 openai_api_key: str,
                 loop: asyncio.AbstractEventLoop,
                 redis_client: Optional['RedisClient'] = None, # Add redis_client here
                 model_name: str = app_config.OPENAI_REALTIME_LLM_MODEL,
                 connect_retries: int = 3,
                 connect_retry_delay_s: float = 2.0,
                 session_inactivity_timeout_s: float = 180.0
                ):
        self.call_specific_prompt: str = call_specific_prompt
        self.api_key: str = openai_api_key
        self.loop: asyncio.AbstractEventLoop = loop
        self.model_name: str = model_name
        
        self._websocket: Optional[websockets.client.WebSocketClientProtocol] = None
        self.is_connected: bool = False
        self.session_id_from_openai: Optional[str] = None
        
        # Redis subscription tasks
        self._injection_listener_task: Optional[asyncio.Task] = None
        self._hitl_events_listener_task: Optional[asyncio.Task] = None
        
        # Queue for AudioSocketHandler to receive synthesized audio from OpenAI
        self.incoming_openai_audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=100) # Maxsize to prevent unbounded growth

        self._receive_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._stop_event = asyncio.Event() # For graceful shutdown
        self._is_terminating = False # Flag to stop sending audio when call is ending
 
        # Retry settings for connection
        self._max_connect_retries: int = connect_retries
        self._base_connect_retry_delay_s: float = connect_retry_delay_s
        self._initial_connection_successful: bool = False # To differentiate initial connect vs. reconnect
        
        # Context for function calling (set by AudioSocketHandler)
        self.call_id: Optional[int] = None
        self.redis_client = redis_client

        logger.info(f"[OpenAIClient:{id(self)}] Initialized for prompt (first 50 chars): '{self.call_specific_prompt[:50]}...'")

    async def connect_and_initialize(self) -> bool:
        """
        Establishes connection to OpenAI, sends session configuration, and starts listening.
        Includes retry logic for the initial connection.
        """
        async with self._connect_lock: # Ensure only one connection attempt at a time
            if self.is_connected:
                logger.warning(f"[OpenAIClient:{self.session_id_from_openai or id(self)}] Already connected.")
                return True

            self._stop_event.clear() # Clear stop event before attempting connection

            for attempt in range(self._max_connect_retries):
                try:
                    logger.info(f"[OpenAIClient:{id(self)}] Attempting to connect to OpenAI (Attempt {attempt + 1}/{self._max_connect_retries})...")
                    headers = {"Authorization": f"Bearer {self.api_key}", "OpenAI-Beta": "realtime=v1"}
                    endpoint = f"wss://api.openai.com/v1/realtime?model={self.model_name}"
                    
                    # Set longer open_timeout, default is 10s, can be too short for first connect under load
                    self._websocket = await websockets.client.connect(endpoint, extra_headers=headers, open_timeout=20.0, ping_interval=20, ping_timeout=20)

                    # Prepare session config
                    session_config = {
                        "type": "session.update",
                        "session": {
                            "modalities": ["audio", "text"],
                            "instructions": self.call_specific_prompt,
                            "voice": "alloy",
                            #"temperature": 0.6,  # Minimum temperature allowed by OpenAI Realtime API (0.6-1.2)
                            "input_audio_format": "pcm16",
                            "output_audio_format": "pcm16",
                            "input_audio_transcription": {
                                "model": "whisper-1",
                                "language": "en"
                            },
                            "tools": [
                                {
                                    "type": "function",
                                    "name": "end_call",
                                    "description": "Terminate the call. This function MUST be used to end all calls.",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {
                                            "final_message": {
                                                "type": "string",
                                                "description": "The exact, final, polite message that you are saying to the user before hanging up (e.g., 'Thank you for your time. Goodbye.'). This is used for timing the hangup."
                                            },
                                            "reason": {
                                                "type": "string",
                                                "description": "A summary of the reason for ending the call."
                                            },
                                            "outcome": {
                                                "type": "string",
                                                "enum": ["success", "failure", "dnd", "user_busy"],
                                                "description": "The final outcome of the call."
                                            }
                                        },
                                        "required": ["final_message", "reason", "outcome"]
                                    }
                                },
                                {
                                    "type": "function",
                                    "name": "send_dtmf",
                                    "description": "Send DTMF tones for menu navigation",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {
                                            "digits": {
                                                "type": "string",
                                                "pattern": "^[0-9*#]+$",
                                                "description": "DTMF digits to send (0-9, *, #)"
                                            }
                                        },
                                        "required": ["digits"]
                                    }
                                },
                                {
                                    "type": "function",
                                    "name": "reschedule_call",
                                    "description": "Schedule a callback for later",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {
                                            "reason": {
                                                "type": "string",
                                                "description": "Reason for rescheduling"
                                            },
                                            "time_description": {
                                                "type": "string",
                                                "description": "When to call back (e.g., 'tomorrow at 10 AM')"
                                            }
                                        },
                                        "required": ["reason", "time_description"]
                                    }
                                },
                                {
                                    "type": "function",
                                    "name": "request_user_info",
                                    "description": "Request real-time information from the task creator during a live call. Use this when you need additional information that wasn't provided in your initial prompt.",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {
                                            "question": {
                                                "type": "string",
                                                "description": "The specific question to ask the task creator"
                                            },
                                            "timeout_seconds": {
                                                "type": "integer",
                                                "default": 10,
                                                "minimum": 5,
                                                "maximum": 30,
                                                "description": "How long to wait for the task creator's response (5-30 seconds)"
                                            },
                                            "recipient_message": {
                                                "type": "string",
                                                "description": "Message to tell the call recipient while waiting (e.g., 'Please hold while I get that information for you')"
                                            }
                                        },
                                        "required": ["question", "recipient_message"]
                                    }
                                }
                           ],
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.3,
                                "prefix_padding_ms": 100,
                                "silence_duration_ms": 1500
                            }
                        }
                    }
                    
                    await self._websocket.send(json.dumps(session_config))
                    logger.info(f"[OpenAIClient:{id(self)}] Sent session.update to OpenAI. Waiting for confirmation...")

                    # Wait for session confirmation (session.success or session.created)
                    response_raw = await asyncio.wait_for(self._websocket.recv(), timeout=15.0)
                    response = json.loads(response_raw)

                    if response.get("type") in ["session.success", "session.created"]:
                        self.session_id_from_openai = response.get('session', {}).get('id', f"client_{id(self)}")
                        self.is_connected = True
                        self._initial_connection_successful = True
                        logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI session successfully started/acknowledged (Type: {response.get('type')}).")
                        
                        # Start the receive loop task
                        if self._receive_task and not self._receive_task.done():
                            self._receive_task.cancel() # Cancel previous if any (e.g. from a reconnect scenario)
                        self._receive_task = self.loop.create_task(self._receive_loop())
                        return True
                    elif response.get("type") == "error":
                        logger.error(f"[OpenAIClient:{id(self)}] OpenAI returned an error after session.update: {response.get('error')}")
                        await self._close_websocket_gracefully() # Close WS before retrying
                        # Fall through to retry logic
                    else:
                        logger.warning(f"[OpenAIClient:{id(self)}] Unexpected response from OpenAI after session.update: {response.get('type')}. Raw: {str(response_raw)[:200]}")
                        await self._close_websocket_gracefully()
                        # Fall through to retry logic

                except websockets.exceptions.InvalidStatusCode as e:
                    logger.error(f"[OpenAIClient:{id(self)}] OpenAI connection failed (HTTP Status {e.status_code}): {e}")
                    if e.status_code == 401:
                        logger.critical(f"[OpenAIClient:{id(self)}] OpenAI Authentication Failed (401). Cannot proceed.")
                        return False # Fatal, no retry
                    # Other status codes might be retriable
                except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
                    logger.warning(f"[OpenAIClient:{id(self)}] OpenAI connection closed during connect/init: {e}. Retrying if applicable.")
                except asyncio.TimeoutError:
                    logger.error(f"[OpenAIClient:{id(self)}] Timeout during OpenAI connection or session confirmation.")
                except Exception as e:
                    logger.error(f"[OpenAIClient:{id(self)}] Error during OpenAI connection attempt {attempt + 1}: {e}", exc_info=True)

                # If loop continues, it means an error occurred or retry is needed
                if attempt < self._max_connect_retries - 1:
                    # Exponential backoff with jitter
                    delay = (self._base_connect_retry_delay_s * (2 ** attempt)) + (time.time() % 1.0)
                    logger.info(f"[OpenAIClient:{id(self)}] Retrying connection in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"[OpenAIClient:{id(self)}] Failed to connect to OpenAI after {self._max_connect_retries} attempts.")
                    return False
            
            # Should not be reached if successful connection returns True inside loop
            return False


    async def send_audio_chunk(self, audio_bytes_24khz_pcm16: bytes):
        if self._is_terminating:
            logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] Call is terminating, ignoring further audio chunks.")
            return

        if not self.is_connected or not self._websocket or self._websocket.closed:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot send audio, not connected.")
            return
 
        try:
            audio_b64 = base64.b64encode(audio_bytes_24khz_pcm16).decode('ascii')
            message = {"type": "input_audio_buffer.append", "audio": audio_b64}
            await self._websocket.send(json.dumps(message))
            #logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] Sent {len(audio_bytes_24khz_pcm16)} bytes (24kHz PCM16) to OpenAI.")
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI connection closed while sending audio: {e}. Triggering reconnect.")
            self.is_connected = False # Mark as disconnected to allow reconnect logic
            # No explicit reconnect call here; _receive_loop's exception handling will manage it.
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error sending audio to OpenAI: {e}", exc_info=True)

    async def trigger_ai_response(self):
        """Send a 'response.create' message to prompt the AI to speak."""
        if not self.is_connected or not self._websocket or self._websocket.closed:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot trigger AI response, not connected.")
            return

        try:
            response_create_payload = {"type": "response.create"}
            await self._websocket.send(json.dumps(response_create_payload))
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Sent 'response.create' to OpenAI to trigger AI's turn.")
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI connection closed while triggering response: {e}.")
            self.is_connected = False
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error triggering AI response: {e}", exc_info=True)

    def set_call_context(self, call_id: int):
        """Set the call ID and start listeners"""
        self.call_id = call_id
        if self.redis_client:
            self._start_injection_listener()
            self._start_hitl_events_listener()
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Set call context for call {call_id}")

    def _start_injection_listener(self):
        """Start Redis listener for injection commands"""
        if self._injection_listener_task and not self._injection_listener_task.done():
            return
            
        if self.call_id and self.redis_client:
            self._injection_listener_task = self.loop.create_task(self._injection_redis_listener())
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Started injection listener for call {self.call_id}")

    async def _injection_redis_listener(self):
        """Listen for injection commands on Redis"""
        if not self.redis_client or not self.call_id:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot start injection listener - missing redis_client or call_id")
            return
            
        try:
            # Subscribe to injection commands for this specific call
            pattern = f"ai_commands:{self.call_id}"
            await self.redis_client.subscribe_to_channel(pattern, self._handle_injection_command)
                    
        except asyncio.CancelledError:
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Injection listener cancelled")
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in injection listener: {e}", exc_info=True)

    async def _handle_injection_command(self, channel: str, data: dict):
        """Handle injection commands from Redis"""
        try:
            command_type = data.get("command_type")
            
            if command_type == "inject_system_message":
                message = data.get("system_message", "")
                trigger_response = data.get("trigger_response", True)
                
                success = await self.inject_system_message(message, trigger_response)
                if success:
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Successfully processed injection command")
                else:
                    logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to process injection command")
            # Handle other existing commands...
            # Note: hitl_response_provided and hitl_request_timed_out are now handled by a separate listener.
            elif command_type in ["end_call", "send_dtmf", "reschedule_call", "request_user_info"]:
                # These are handled by the function calling mechanism, not injection.
                pass
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error handling injection command: {e}", exc_info=True)

    async def inject_system_message(self, message: str, trigger_response: bool = True) -> bool:
        """
        Inject a system message into the live conversation using OpenAI Realtime API.
        This allows providing context to the AI during an active call.
        """
        if not self.is_connected or not self._websocket or self._websocket.closed:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot inject system message - not connected to OpenAI")
            return False
        
        try:
            # Use OpenAI Realtime API conversation.item.create format
            system_item = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": message}]
                }
            }
            
            await self._websocket.send(json.dumps(system_item))
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Injected system message: {message[:100]}...")
            
            if trigger_response:
                # Give a small delay for the system message to be processed
                await asyncio.sleep(0.1)
                await self.trigger_ai_response()
                
            return True
            
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error injecting system message: {e}", exc_info=True)
            return False

    def _start_hitl_events_listener(self):
        """Start Redis listener for HITL events"""
        if self._hitl_events_listener_task and not self._hitl_events_listener_task.done():
            return
            
        if self.call_id and self.redis_client:
            self._hitl_events_listener_task = self.loop.create_task(self._hitl_redis_listener())
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Started HITL events listener for call {self.call_id}")

    async def _hitl_redis_listener(self):
        """Listen for HITL event commands on Redis"""
        if not self.redis_client or not self.call_id:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot start HITL listener - missing redis_client or call_id")
            return
            
        try:
            # Subscribe to HITL events for this specific call
            pattern = f"hitl_events:{self.call_id}"
            await self.redis_client.subscribe_to_channel(pattern, self._handle_hitl_event_command)
                    
        except asyncio.CancelledError:
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] HITL events listener cancelled")
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in HITL events listener: {e}", exc_info=True)

    async def _handle_hitl_event_command(self, channel: str, data: dict):
        """Handle HITL event commands from Redis"""
        try:
            command_type = data.get("command_type")
            
            if command_type == "hitl_response_provided":
                response_text = data.get("response", "No response text provided.")
                system_message = f"""HUMAN-IN-THE-LOOP RESPONSE:
The task creator has provided the following information: "{response_text}"
INSTRUCTIONS:
1. Acknowledge you have the information.
2. Use this information to continue the conversation and complete your objective.
3. Do not mention the 'human-in-the-loop' or 'task creator' to the user. Simply use the information naturally."""
                await self.inject_system_message(system_message)
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Injected HITL response into conversation.")

            elif command_type == "hitl_request_timed_out":
                question_text = data.get("question", "an unspecified question")
                system_message = f"""HUMAN-IN-THE-LOOP TIMEOUT:
The task creator did not respond in time to your question: "{question_text}"
INSTRUCTIONS:
1. Acknowledge the timeout internally. Do not mention it to the user.
2. Use your best judgment to make a sensible decision and continue the call.
3. You could say something like, "I'll proceed with the information I have," or make a reasonable default choice.
4. Continue with the call objective autonomously."""
                await self.inject_system_message(system_message)
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Injected HITL timeout message into conversation.")
            
            else:
                logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Unknown HITL event command type: {command_type}")
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error handling HITL event command: {e}", exc_info=True)

    async def _receive_loop(self):
        logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Starting OpenAI receive loop.")
        try:
            while not self._stop_event.is_set() and self._websocket and not self._websocket.closed:
                try:
                    # Set a timeout for recv() to allow periodic check of _stop_event
                    message_raw = await asyncio.wait_for(self._websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue # Check _stop_event and loop again
                
                data = json.loads(message_raw)
                msg_type = data.get("type")

                if msg_type == "error":
                    logger.error(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI API Error: {data.get('error', data)}")
                    # Depending on error, might need to close or attempt recovery
                    if "session" in str(data.get('error','')).lower() and "not found" in str(data.get('error','')).lower():
                        logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Fatal session error. Stopping client.")
                        self._stop_event.set() # Stop the client, session is invalid
                        break
                elif msg_type == "response.audio.delta":
                    audio_data_b64 = data.get("delta")
                    if audio_data_b64:
                        try:
                            # OpenAI sends PCM16 at 24kHz as per our `output_audio_format`
                            ai_audio_bytes_24khz_pcm16 = base64.b64decode(audio_data_b64)
                            if ai_audio_bytes_24khz_pcm16:
                                await self.incoming_openai_audio_queue.put(ai_audio_bytes_24khz_pcm16)
                                logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] Queued {len(ai_audio_bytes_24khz_pcm16)} bytes of AI audio (24kHz). Queue size: {self.incoming_openai_audio_queue.qsize()}")
                        except Exception as e_audio_q:
                            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error processing/queuing AI audio delta: {e_audio_q}", exc_info=True)
                
                elif msg_type == "response.audio_transcript.delta":
                    delta_content = data.get('delta')
                    transcript_text = ""
                    if isinstance(delta_content, dict): transcript_text = delta_content.get('text', '')
                    elif isinstance(delta_content, str): transcript_text = delta_content
                    if transcript_text: logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Tx Delta: \"{transcript_text}\"")

                elif msg_type == "response.audio_transcript.done":
                    transcript_content = data.get('transcript')
                    full_transcript = "[No full transcript text provided]"
                    if isinstance(transcript_content, dict): full_transcript = transcript_content.get('text', '[No text in dict]')
                    elif isinstance(transcript_content, str): full_transcript = transcript_content
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Tx FINAL: \"{full_transcript}\"")
                    # Save assistant transcript to database
                    if full_transcript.strip() and full_transcript != "[No full transcript text provided]":
                        asyncio.create_task(self._save_transcript_to_db("agent", full_transcript))
                
                elif msg_type == "response.done":
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Event: response.done (AI turn finished).")
                
                elif msg_type == "response.function_call_output":
                    function_call_data = data.get("output")
                    if function_call_data:
                        logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Function call received: {function_call_data}")
                        asyncio.create_task(self._execute_function_call(function_call_data, data.get("call_id")))

                elif msg_type == "response.function_call_arguments.done":
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Received function call arguments done event: {str(message_raw)[:500]}")
                    function_name = data.get("name")
                    arguments_str = data.get("arguments", "{}")
                    openai_func_call_id = data.get("call_id")
                    
                    try:
                        arguments = json.loads(arguments_str)
                        function_call_data = {
                            "name": function_name,
                            "arguments": arguments
                        }
                        asyncio.create_task(self._execute_function_call(function_call_data, openai_func_call_id))
                    except json.JSONDecodeError:
                        logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to parse function call arguments JSON: {arguments_str}")
                
                elif msg_type == "conversation.item.input_audio_transcription.completed":
                    user_transcript = data.get('transcript', '')
                    if user_transcript.strip():
                        logger.info(f"[OpenAIClient:{self.session_id_from_openai}] User said: \"{user_transcript}\"")
                        asyncio.create_task(self._save_transcript_to_db("user", user_transcript))
                
                # Log other relevant messages for debugging, less verbosely for frequent ones
                elif msg_type in ["input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped", 
                                  "session.updated", "session.created", "response.created", "response.audio.done"]:
                    logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Event: Type='{msg_type}', Snippet='{str(message_raw)[:120]}...'")
                elif msg_type in ["input_audio_buffer.committed", "conversation.item.created", 
                                  "response.output_item.added", "response.content_part.added", 
                                  "response.content_part.done", "response.output_item.done", 
                                  "rate_limits.updated"]:
                    logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Info Event: '{msg_type}'")
                else:
                    # Enhanced logging to catch function call events we might be missing
                    if "function" in msg_type.lower() or "call" in msg_type.lower():
                        logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] *** POTENTIAL FUNCTION CALL EVENT *** Type='{msg_type}': {str(message_raw)[:500]}...")
                    else:
                        logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Unknown Msg Type '{msg_type}': {str(message_raw)[:200]}...")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI WebSocket closed in _receive_loop (Code: {e.code}, Reason: '{e.reason}').")
            self.is_connected = False
            if not self._stop_event.is_set(): # If not a deliberate shutdown
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Attempting to handle disconnect and reconnect...")
                asyncio.create_task(self._handle_disconnect_and_reconnect()) # Don't await here
        except asyncio.CancelledError:
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI receive loop cancelled.")
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in OpenAI receive loop: {e}", exc_info=True)
            self.is_connected = False
            if not self._stop_event.is_set():
                 asyncio.create_task(self._handle_disconnect_and_reconnect()) # Don't await
        finally:
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI receive loop finished.")
            # Signal consumer that there's no more audio by putting None
            await self.incoming_openai_audio_queue.put(None)


    async def _handle_disconnect_and_reconnect(self):
        if self._stop_event.is_set(): # Don't try to reconnect if we are stopping
            return

        logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI connection lost. Attempting reconnect sequence...")
        # Mark as disconnected immediately
        self.is_connected = False
        if self._websocket and not self._websocket.closed:
            await self._close_websocket_gracefully()
        self._websocket = None

        # Only attempt reconnect if initial connection was ever successful
        # This prevents looping if the very first connection fails repeatedly due to fatal issues (e.g. bad API key)
        if not self._initial_connection_successful:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Initial connection never succeeded. Halting reconnect attempts.")
            self._stop_event.set() # Signal client to fully stop
            return

        # Attempt to reconnect
        # We use connect_and_initialize which has its own retry logic.
        # We might want a small delay before the first attempt of THIS reconnect sequence.
        await asyncio.sleep(self._base_connect_retry_delay_s / 2) 
        
        logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Calling connect_and_initialize for reconnection.")
        reconnected = await self.connect_and_initialize() # This will retry internally

        if reconnected:
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Successfully reconnected to OpenAI.")
        else:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to reconnect to OpenAI after retries. Client will remain disconnected.")
            self._stop_event.set() # Signal client to fully stop if reconnect fails

    async def get_synthesized_audio_chunk(self) -> Optional[bytes]:
        """
        Retrieves the next available synthesized audio chunk from OpenAI.
        Blocks if the queue is empty, until an item is available or None (for EOS).
        Returns None if the client is stopping or has stopped, signaling end of stream.
        """
        if self._stop_event.is_set() and self.incoming_openai_audio_queue.empty():
            return None
        try:
            # Timeout to allow checking _stop_event periodically if queue is persistently empty
            chunk = await asyncio.wait_for(self.incoming_openai_audio_queue.get(), timeout=0.5)
            if chunk is not None:
                self.incoming_openai_audio_queue.task_done()
            return chunk
        except asyncio.TimeoutError:
            return None # No audio chunk available within timeout
        except asyncio.CancelledError:
             logger.info(f"[OpenAIClient:{self.session_id_from_openai}] get_synthesized_audio_chunk cancelled.")
             return None


    async def _close_websocket_gracefully(self):
        if self._websocket and not self._websocket.closed:
            try:
                # OpenAI Realtime API does not specify a "goodbye" message.
                # Closing the WebSocket is standard.
                await self._websocket.close(code=1000, reason="Client shutdown")
                logger.info(f"[OpenAIClient:{self.session_id_from_openai or id(self)}] WebSocket closed gracefully.")
            except Exception as e:
                logger.warning(f"[OpenAIClient:{self.session_id_from_openai or id(self)}] Error closing WebSocket: {e}")
        self._websocket = None


    async def close(self):
        logger.info(f"[OpenAIClient:{self.session_id_from_openai or id(self)}] Closing OpenAI client...")
        self._stop_event.set() # Signal all loops to stop
        
        # Stop HITL events listener
        if self._hitl_events_listener_task and not self._hitl_events_listener_task.done():
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Cancelling HITL events listener...")
            self._hitl_events_listener_task.cancel()
            try:
                await self._hitl_events_listener_task
            except asyncio.CancelledError:
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] HITL events listener successfully cancelled.")
            except Exception as e:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error cancelling HITL events listener: {e}")

        # Stop injection listener
        if self._injection_listener_task and not self._injection_listener_task.done():
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Cancelling injection listener...")
            self._injection_listener_task.cancel()
            try:
                await self._injection_listener_task
            except asyncio.CancelledError:
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Injection listener successfully cancelled.")
            except Exception as e:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error cancelling injection listener: {e}")

        if self._receive_task and not self._receive_task.done():
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Cancelling receive task...")
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Receive task successfully cancelled.")
            except Exception as e_await:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error awaiting cancelled receive task: {e_await}")
        
        await self._close_websocket_gracefully()
        
        self.is_connected = False
        self._initial_connection_successful = False # Reset for potential re-use if object is kept
        
        # Ensure queue is emptied and EOS marker is present if not already
        # This helps any consumer stuck on queue.get() to unblock
        while not self.incoming_openai_audio_queue.empty():
            try:
                self.incoming_openai_audio_queue.get_nowait()
                self.incoming_openai_audio_queue.task_done()
            except asyncio.QueueEmpty:
                break
        try:
            self.incoming_openai_audio_queue.put_nowait(None) # Ensure EOS marker
        except asyncio.QueueFull:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Audio queue full during shutdown, cannot place EOS marker.")


        logger.info(f"[OpenAIClient:{self.session_id_from_openai or id(self)}] OpenAI client closed.")

    async def _execute_function_call(self, function_call_data: dict, call_id: Optional[str] = None):
        """Execute function calls from OpenAI and send results back"""
        try:
            function_name = function_call_data.get("name")
            arguments = function_call_data.get("arguments", {})
            
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] *** EXECUTING FUNCTION CALL *** Function: {function_name}, Args: {arguments}")
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Context check - call_id: {self.call_id}, redis_client: {self.redis_client is not None}")
            
            if function_name == "end_call":
                result = await self._execute_end_call(arguments)
                # Send function result back to OpenAI
                await self._send_function_result(call_id, function_name, result)
            elif function_name == "send_dtmf":
                result = await self._execute_send_dtmf(arguments)
                await self._send_function_result(call_id, function_name, result)
            elif function_name == "reschedule_call":
                result = await self._execute_reschedule_call(arguments)
                await self._send_function_result(call_id, function_name, result)
            elif function_name == "request_user_info":
                result = await self._execute_request_user_info(arguments)
                await self._send_function_result(call_id, function_name, result)
            else:
                logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Unknown function: {function_name}")
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error executing function call: {e}", exc_info=True)

    async def _execute_end_call(self, arguments: dict) -> dict:
        """Execute end_call function and publish Redis command"""
        try:
            self._is_terminating = True # Set the flag to stop sending further audio
            logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Terminating flag set. No more audio will be sent to OpenAI.")
            final_message = arguments.get("final_message", "Thank you. Goodbye.")
            reason = arguments.get("reason", "AI decided to end call")
            outcome = arguments.get("outcome", "success")
            
            # Import here to avoid circular imports
            from common.data_models import RedisEndCallCommand
            
            if self.call_id and self.redis_client:
                command = RedisEndCallCommand(
                    call_attempt_id=self.call_id,
                    reason=reason,
                    outcome=outcome,
                    final_message=final_message # Pass the final message in the command
                )
                
                # Publish Redis command to trigger CallAttemptHandler hangup
                channel = f"call_commands:{self.call_id}"
                success = await self.redis_client.publish_command(channel, command.model_dump())
                
                if success:
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Published end_call command. Reason: {reason}, Outcome: {outcome}")
                    return {"status": "success", "message": f"Call termination initiated. {reason}"}
                else:
                    logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to publish end_call command")
                    return {"status": "error", "message": "Failed to terminate call"}
            else:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] No call_id or Redis client available for end_call")
                return {"status": "error", "message": "System error: cannot terminate call"}
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in _execute_end_call: {e}", exc_info=True)
            return {"status": "error", "message": "Internal error executing end_call"}

    async def _execute_send_dtmf(self, arguments: dict) -> dict:
        """Execute send_dtmf function and publish Redis command"""
        try:
            digits = arguments.get("digits", "")
            
            from common.data_models import RedisDTMFCommand
            
            if self.call_id and self.redis_client:
                command = RedisDTMFCommand(
                    call_attempt_id=self.call_id,
                    digits=digits
                )
                
                channel = f"call_commands:{self.call_id}"
                success = await self.redis_client.publish_command(channel, command.model_dump())
                
                if success:
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Published send_dtmf command. Digits: {digits}")
                    return {"status": "success", "message": f"DTMF digits {digits} sent"}
                else:
                    logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to publish send_dtmf command")
                    return {"status": "error", "message": "Failed to send DTMF"}
            else:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] No call_id or Redis client available for send_dtmf")
                return {"status": "error", "message": "System error: cannot send DTMF"}
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in _execute_send_dtmf: {e}", exc_info=True)
            return {"status": "error", "message": "Internal error executing send_dtmf"}

    async def _execute_reschedule_call(self, arguments: dict) -> dict:
        """Execute reschedule_call function and publish Redis command"""
        try:
            reason = arguments.get("reason", "User requested callback")
            time_description = arguments.get("time_description", "later")
            
            from common.data_models import RedisRescheduleCommand
            
            if self.call_id and self.redis_client:
                command = RedisRescheduleCommand(
                    call_attempt_id=self.call_id,
                    reason=reason,
                    time_description=time_description
                )
                
                channel = f"call_commands:{self.call_id}"
                success = await self.redis_client.publish_command(channel, command.model_dump())
                
                if success:
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Published reschedule_call command. Reason: {reason}, Time: {time_description}")
                    return {"status": "success", "message": f"Call rescheduled for {time_description}"}
                else:
                    logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to publish reschedule_call command")
                    return {"status": "error", "message": "Failed to reschedule call"}
            else:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] No call_id or Redis client available for reschedule_call")
                return {"status": "error", "message": "System error: cannot reschedule call"}
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in _execute_reschedule_call: {e}", exc_info=True)
            return {"status": "error", "message": "Internal error executing reschedule_call"}

    async def _execute_request_user_info(self, arguments: dict) -> dict:
        """Execute request_user_info function and publish Redis command for HITL"""
        try:
            question = arguments.get("question", "")
            timeout_seconds = arguments.get("timeout_seconds", 10)
            recipient_message = arguments.get("recipient_message", "Please hold while I get that information for you.")
            
            # Import here to avoid circular imports
            from common.data_models import RedisRequestUserInfoCommand
            
            if self.call_id and self.redis_client:
                command = RedisRequestUserInfoCommand(
                    call_attempt_id=self.call_id,
                    question=question,
                    timeout_seconds=timeout_seconds,
                    recipient_message=recipient_message
                )
                
                # Publish Redis command to orchestrator for HITL processing
                channel = f"call_commands:{self.call_id}"
                success = await self.redis_client.publish_command(channel, command.model_dump())
                
                if success:
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Published request_user_info command. Question: {question}")
                    return {
                        "status": "success",
                        "message": f"Information request sent to task creator. {recipient_message}",
                        "question": question,
                        "timeout_seconds": timeout_seconds
                    }
                else:
                    logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Failed to publish request_user_info command")
                    return {"status": "error", "message": "Failed to send information request"}
            else:
                logger.error(f"[OpenAIClient:{self.session_id_from_openai}] No call_id or Redis client available for request_user_info")
                return {"status": "error", "message": "System error: cannot request information"}
                
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error in _execute_request_user_info: {e}", exc_info=True)
            return {"status": "error", "message": "Internal error executing request_user_info"}

    async def _send_function_result(self, call_id: Optional[str], function_name: str, result: dict):
        """Send function execution result back to OpenAI"""
        try:
            if self._websocket and not self._websocket.closed and call_id:
                result_message = {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result)
                    }
                }
                await self._websocket.send(json.dumps(result_message))
                logger.info(f"[OpenAIClient:{self.session_id_from_openai}] Sent function result for {function_name}")
            else:
                logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot send function result, WebSocket not connected or no call_id")
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error sending function result: {e}", exc_info=True)

    async def _save_transcript_to_db(self, speaker: str, message: str):
        """Save transcript entry to database with proper ordering"""
        try:
            if self.call_id:
                # Import db_manager here to avoid circular imports
                from database import db_manager
                
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, db_manager.save_call_transcript,
                                         self.call_id, speaker, message)
                logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] Saved transcript: {speaker}: {message[:50]}...")
            else:
                logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot save transcript, no call_id available")
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error saving transcript: {e}", exc_info=True)


# --- Basic Test Block ---
async def main_test_openai_client():
    if not app_config.OPENAI_API_KEY:
        print("OPENAI_API_KEY not set in .env. Skipping test.")
        return

    print("Starting OpenAIRealtimeClient Test...")
    test_prompt = "You are a friendly AI assistant. Your name is Alex. Keep responses very short and conversational."
    
    client = OpenAIRealtimeClient(
        call_specific_prompt=test_prompt,
        openai_api_key=app_config.OPENAI_API_KEY,
        loop=asyncio.get_running_loop(),
        redis_client=None # Not needed for this basic test
    )

    if not await client.connect_and_initialize():
        print("Failed to connect to OpenAI. Exiting test.")
        return

    print(f"Successfully connected to OpenAI. Session ID: {client.session_id_from_openai}")

    async def consume_ai_audio():
        print("Audio consumer started. Waiting for AI audio...")
        chunk_count = 0
        total_bytes = 0
        try:
            while True:
                audio_chunk = await client.get_synthesized_audio_chunk()
                if audio_chunk is None: # EOS marker
                    print("Audio consumer received EOS. Exiting.")
                    break
                chunk_count +=1
                total_bytes += len(audio_chunk)
                print(f"Consumer: Received AI audio chunk {chunk_count}, size: {len(audio_chunk)} bytes. Total: {total_bytes}")
                await asyncio.sleep(0.01) # Simulate processing
        except asyncio.CancelledError:
            print("Audio consumer cancelled.")

    consumer_task = asyncio.create_task(consume_ai_audio())

    # Simulate sending some audio to trigger a response.
    # For a real test, this would be actual 24kHz PCM16 audio.
    # Here, we send a small silent chunk just to kick off an interaction if possible,
    # or we could send a text message if OpenAI supports it in this specific Realtime API version
    # (check OpenAI docs for sending text via this specific API).
    # Based on asty.py, it seems primarily audio-driven.

    # Let's send a short silent audio chunk to signify start of user speech
    # This might be enough to trigger a greeting from the AI based on its prompt.
    silent_chunk_20ms_24khz_pcm16 = b'\x00\x00' * int(24000 * 0.02) # 20ms of silence
    
    print("Sending a short silent audio chunk to OpenAI to potentially elicit a greeting...")
    await client.send_audio_chunk(silent_chunk_20ms_24khz_pcm16)
    
    # Give OpenAI some time to process and respond
    await asyncio.sleep(10) # Wait for some audio to be generated and consumed

    print("Test duration elapsed. Closing client...")
    await client.close()
    
    if consumer_task and not consumer_task.done():
        consumer_task.cancel()
        await consumer_task
    
    print("OpenAIRealtimeClient Test Finished.")



if __name__ == "__main__":
    # The sys.path modification is now at the top for standalone.
    # The re-imports of app_config and logger_setup here are not strictly needed
    # if the top-level ones worked, but harmless for clarity during testing.
    # from config.app_config import app_config # Can be removed if top-level works
    # from common.logger_setup import setup_logger # Can be removed
    
    # Ensure logger for test is using the potentially re-imported app_config
    test_logger_standalone = setup_logger("OpenAIClientStandaloneTest", level_str=app_config.LOG_LEVEL if 'app_config' in locals() else "DEBUG")

    try:
        asyncio.run(main_test_openai_client())
    except KeyboardInterrupt:
        test_logger_standalone.info("Test interrupted by user.")
    except Exception as e:
        test_logger_standalone.error(f"Error running test: {e}", exc_info=True)