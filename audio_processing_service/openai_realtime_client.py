# audio_processing_service/openai_realtime_client.py
import asyncio
import json
import base64
import websockets
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

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class OpenAIRealtimeClient:
    def __init__(self,
                 call_specific_prompt: str,
                 openai_api_key: str,
                 loop: asyncio.AbstractEventLoop,
                 model_name: str = app_config.OPENAI_REALTIME_LLM_MODEL,
                 connect_retries: int = 3,
                 connect_retry_delay_s: float = 2.0,
                 session_inactivity_timeout_s: float = 180.0
                ):
        self.call_specific_prompt: str = call_specific_prompt
        self.api_key: str = openai_api_key
        self.loop: asyncio.AbstractEventLoop = loop
        self.model_name: str = model_name
        
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected: bool = False
        self.session_id_from_openai: Optional[str] = None
        
        # Queue for AudioSocketHandler to receive synthesized audio from OpenAI
        self.incoming_openai_audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=100) # Maxsize to prevent unbounded growth

        self._receive_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._stop_event = asyncio.Event() # For graceful shutdown

        # Retry settings for connection
        self._max_connect_retries: int = connect_retries
        self._base_connect_retry_delay_s: float = connect_retry_delay_s
        self._initial_connection_successful: bool = False # To differentiate initial connect vs. reconnect

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
                    self._websocket = await websockets.connect(endpoint, extra_headers=headers, open_timeout=20.0, ping_interval=20, ping_timeout=20)

                    # Prepare session config
                    session_config = {
                        "type": "session.update",
                        "session": {
                            "modalities": ["audio", "text"],
                            "instructions": self.call_specific_prompt,
                            "voice": "alloy",
                            "input_audio_format": "pcm16",
                            "output_audio_format": "pcm16",
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
                    logger.error(f"[OpenAIClient:{id(self)}] OpenAI connection failed (HTTP Status {e.status_code}): {e.body.decode() if hasattr(e, 'body') and e.body else 'No body'}")
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
        if not self.is_connected or not self._websocket or self._websocket.closed:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] Cannot send audio, not connected.")
            return

        try:
            audio_b64 = base64.b64encode(audio_bytes_24khz_pcm16).decode('ascii')
            message = {"type": "input_audio_buffer.append", "audio": audio_b64}
            await self._websocket.send(json.dumps(message))
            logger.debug(f"[OpenAIClient:{self.session_id_from_openai}] Sent {len(audio_bytes_24khz_pcm16)} bytes (24kHz PCM16) to OpenAI.")
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI connection closed while sending audio: {e}. Triggering reconnect.")
            self.is_connected = False # Mark as disconnected to allow reconnect logic
            # No explicit reconnect call here; _receive_loop's exception handling will manage it.
        except Exception as e:
            logger.error(f"[OpenAIClient:{self.session_id_from_openai}] Error sending audio to OpenAI: {e}", exc_info=True)


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
                
                elif msg_type == "response.done":
                    logger.info(f"[OpenAIClient:{self.session_id_from_openai}] OpenAI Event: response.done (AI turn finished).")
                
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
        loop=asyncio.get_running_loop()
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
    # or we could send a text message if OpenAI supports it in this Realtime API version
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