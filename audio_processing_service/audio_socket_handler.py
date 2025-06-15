# audio_processing_service/audio_socket_handler.py
import asyncio
import struct
import sys
import uuid # For uuid.UUID()
from pathlib import Path
from typing import Optional
import numpy as np
import os
import wave # For saving WAV files

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from common.data_models import RedisEndCallCommand
from database import db_manager
from database.models import CallStatus

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

# Constants for audio processing
TARGET_ASTERISK_CHUNK_SIZE_BYTES = 320  # 20ms of 8kHz, 16-bit PCM
PCM_SAMPLE_WIDTH_BYTES = 2  # Bytes per sample for 16-bit PCM
AST_SAMPLE_RATE = 8000
OPENAI_SAMPLE_RATE = 24000  # Assuming OpenAI will use 24kHz

# Asterisk AudioSocket message types (for TCP protocol)
TYPE_HANGUP = 0x00
TYPE_UUID = 0x01   # Asterisk sends the Dialplan-provided UUID in the first frame with this type.
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xff

# Audio utility functions
def resample_audio(audio_np: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resample audio from source sample rate to destination sample rate."""
    if src_sr == dst_sr or audio_np.size == 0:
        return audio_np.astype(np.int16)
    
    num_samples_dst = int(audio_np.size * dst_sr / src_sr)
    if num_samples_dst == 0:
        return np.array([], dtype=np.int16)
    
    if audio_np.size == 1:
        return np.full(num_samples_dst, audio_np[0], dtype=np.int16)
    
    x_src_indices = np.arange(audio_np.size)
    x_dst_indices = np.linspace(0, audio_np.size - 1, num_samples_dst, endpoint=True)
    resampled_audio = np.interp(x_dst_indices, x_src_indices, audio_np)
    
    return np.round(resampled_audio).astype(np.int16)

class AudioSocketHandler:
    def __init__(self,
                 reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 redis_client: RedisClient, # Initial params for TCP version
                 peername: tuple | str | None):
        self.reader = reader
        self.writer = writer
        self.redis_client = redis_client
        self.peername = peername if peername else "UnknownPeer"
        
        # These will be populated after reading the first TYPE_UUID frame from Asterisk
        self.call_id: Optional[int] = None
        self.asterisk_call_uuid: Optional[str] = None # UUID from dialplan, received in first frame

        self._stop_event = asyncio.Event()
        self._redis_listener_task: Optional[asyncio.Task] = None
        self._incoming_audio_frames: List[bytes] = [] # Buffer for incoming audio
        self._openai_ready = False
        self.openai_client = None
        self.loop = asyncio.get_running_loop()
        
        # Initialize playback buffer and lock
        self.playback_buffer_8khz = bytearray()
        self.playback_buffer_lock = asyncio.Lock()
        
        # Initialize audio buffers for recording
        self.session_caller_audio_buffer = []  # Buffer for caller audio (24kHz)
        self.session_ai_audio_buffer = []      # Buffer for AI audio (24kHz)
        self.session_lock = asyncio.Lock()     # Lock for audio buffers
        
        # Pre-generate silent frame for keeping connection alive
        self.silent_frame = bytes([0] * TARGET_ASTERISK_CHUNK_SIZE_BYTES)
        self.silent_frame_header = struct.pack("!BH", TYPE_AUDIO, TARGET_ASTERISK_CHUNK_SIZE_BYTES)
        
        # OpenAI receive task
        self._openai_receive_task = None
        
        logger.info(f"[AudioSocketHandler-TCP:Peer={self.peername}] Initialized with test tone buffer, awaiting initial UUID frame from Asterisk.")

    async def _update_call_status_db(self, status: CallStatus, **kwargs):
        """Helper to update call status in DB asynchronously."""
        if self.call_id is None or self.asterisk_call_uuid is None:
            logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername}] Cannot update DB status. CallID/AsteriskUUID not yet identified.")
            return

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                db_manager.update_call_status,
                self.call_id,                     # Use internal integer AppCallID for DB PK
                status,
                kwargs.get("hangup_cause"),
                kwargs.get("call_conclusion"),
                kwargs.get("duration_seconds"),
                kwargs.get("asterisk_channel"),    # This might be hard to get in TCP mode directly
                self.asterisk_call_uuid           # Store the Dialplan UUID in the 'call_uuid' DB column
            )
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Call status updated to {status.value}")
        except Exception as e:
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Error updating DB status to {status.value}: {e}", exc_info=True)

    async def _handle_redis_command(self, channel: str, command_data_dict: dict):
        if self.call_id is None: # Ensure call_id is set before processing commands
            logger.warning(f"[AudioSocketHandler-TCP:AstDialplanUUID={self.asterisk_call_uuid or 'Unknown'}] Received Redis command but AppCallID not set. Ignoring.")
            return

        logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Received Redis command on {channel}: {command_data_dict}")
        command_type = command_data_dict.get("command_type")
        if command_type == RedisEndCallCommand.model_fields['command_type'].default:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Received EndCall command via Redis. Signaling handler to stop.")
            self._stop_event.set()
        
    async def _listen_for_redis_commands(self):
        if self.call_id is None: # Should not happen if called correctly
            logger.error(f"[AudioSocketHandler-TCP:AstDialplanUUID={self.asterisk_call_uuid or 'Unknown'}] Cannot start Redis listener, AppCallID not identified.")
            return

        redis_channel_pattern = f"audiosocket_commands:{self.call_id}"
        logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Subscribing to Redis channel: {redis_channel_pattern}")
        try:
            await self.redis_client.subscribe_to_channel(redis_channel_pattern, self._handle_redis_command)
        except asyncio.CancelledError:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Redis listener task cancelled.")
        except Exception as e:
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Error in Redis listener for {redis_channel_pattern}: {e}", exc_info=True)
            if not self._stop_event.is_set(): self._stop_event.set() 
        finally:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Redis listener stopped for {redis_channel_pattern}.")


    async def _listen_for_openai_responses(self):
        """Listen for audio responses from OpenAI and add them to the playback buffer"""
        logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Starting OpenAI response listener")
        try:
            while not self._stop_event.is_set() and self.openai_client and self.openai_client.is_connected:
                try:
                    # Get audio from OpenAI (24kHz)
                    audio_chunk = await self.openai_client.get_synthesized_audio_chunk()
                    if audio_chunk is None:
                        # End of stream or timeout
                        await asyncio.sleep(0.01)
                        continue
                    
                    # Store original 24kHz audio for recording
                    ai_audio_np_24khz = np.frombuffer(audio_chunk, dtype=np.int16)
                    async with self.session_lock:
                        self.session_ai_audio_buffer.append(ai_audio_np_24khz.copy())
                    
                    # Resample to 8kHz for Asterisk
                    ai_audio_np_8khz = resample_audio(ai_audio_np_24khz, OPENAI_SAMPLE_RATE, AST_SAMPLE_RATE)
                    
                    # Apply gain
                    gain_factor = 2.0  # Adjust as needed
                    ai_audio_float = ai_audio_np_8khz.astype(np.float32) * gain_factor
                    ai_audio_float = np.clip(ai_audio_float, -32768.0, 32767.0)
                    ai_audio_8khz = ai_audio_float.astype(np.int16).tobytes()
                    
                    # Add to playback buffer
                    async with self.playback_buffer_lock:
                        self.playback_buffer_8khz.extend(ai_audio_8khz)
                    
                    logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Added {len(ai_audio_8khz)} bytes of OpenAI audio to playback buffer")
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Error processing OpenAI audio: {e}")
                    await asyncio.sleep(0.1)  # Prevent tight loop on error
        except asyncio.CancelledError:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] OpenAI response listener cancelled")
        except Exception as e:
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Error in OpenAI response listener: {e}")
        finally:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] OpenAI response listener finished")
    
    async def _save_incoming_audio_as_wav(self):
        """Save collected audio frames as a WAV file.
        Creates a stereo WAV with caller audio in left channel and AI audio in right channel."""
        # Create recordings directory if it doesn't exist
        recordings_dir = Path(_project_root) / "recordings"
        recordings_dir.mkdir(exist_ok=True)

        # Generate filename with call identification
        timestamp = asyncio.get_running_loop().time()
        filename = f"call_{self.call_id}_{self.asterisk_call_uuid}_{int(timestamp)}.wav"
        wav_path = recordings_dir / filename

        try:
            # Process caller and AI audio buffers
            final_caller_audio = np.array([], dtype=np.int16)
            final_ai_audio = np.array([], dtype=np.int16)
            
            async with self.session_lock:
                if self.session_caller_audio_buffer:
                    final_caller_audio = np.concatenate(self.session_caller_audio_buffer)
                if self.session_ai_audio_buffer:
                    final_ai_audio = np.concatenate(self.session_ai_audio_buffer)
            
            if final_caller_audio.size == 0 and final_ai_audio.size == 0:
                logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] No audio data recorded for this session. Skipping WAV file generation.")
                return
            
            # Create stereo audio (caller in left channel, AI in right channel)
            maxlen = max(final_caller_audio.size, final_ai_audio.size)
            if final_caller_audio.size < maxlen:
                final_caller_audio = np.pad(final_caller_audio, (0, maxlen - final_caller_audio.size), 'constant', constant_values=0)
            if final_ai_audio.size < maxlen:
                final_ai_audio = np.pad(final_ai_audio, (0, maxlen - final_ai_audio.size), 'constant', constant_values=0)
            
            stereo_audio = np.stack((final_caller_audio, final_ai_audio), axis=-1).astype(np.int16)
            
            # Write stereo WAV file
            with wave.open(str(wav_path), 'wb') as wav_file:
                wav_file.setnchannels(2)  # Stereo audio
                wav_file.setsampwidth(2)  # 2 bytes per sample (16-bit PCM)
                wav_file.setframerate(OPENAI_SAMPLE_RATE)  # 24kHz sample rate
                wav_file.writeframes(stereo_audio.tobytes())

            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Saved stereo call recording to {wav_path}")
        except Exception as e:
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Error saving audio file: {e}", exc_info=True)

    async def _send_audio_to_asterisk_task(self):
        """Dedicated task to continuously send audio to Asterisk"""
        logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Starting audio send task")
        try:
            while not self._stop_event.is_set():
                if self.writer and not self.writer.is_closing():
                    # Check playback buffer first
                    chunk_to_send = None
                    async with self.playback_buffer_lock:
                        if len(self.playback_buffer_8khz) >= TARGET_ASTERISK_CHUNK_SIZE_BYTES:
                            chunk_to_send = self.playback_buffer_8khz[:TARGET_ASTERISK_CHUNK_SIZE_BYTES]
                            del self.playback_buffer_8khz[:TARGET_ASTERISK_CHUNK_SIZE_BYTES]
                    
                    if chunk_to_send:
                        # Send buffered audio
                        header = struct.pack("!BH", TYPE_AUDIO, len(chunk_to_send))
                        self.writer.write(header + chunk_to_send)
                        await self.writer.drain()
                        logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Sent buffered audio frame")
                    else:
                        # Send pre-generated silent frame to maintain connection
                        self.writer.write(self.silent_frame_header + self.silent_frame)
                        await self.writer.drain()
                        logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Sent silent frame")
                    
                    await asyncio.sleep(0.015)  # Crucial timing
                else:
                    break
        except Exception as e:
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Error in audio send task: {e}")
        finally:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Audio send task finished")

    async def handle_frames(self):
        """Main loop to handle incoming frames from Asterisk (TCP AudioSocket protocol)."""
        audio_send_task = None
        try:
            # --- Stage 1: Read the initial TYPE_UUID frame from Asterisk ---
            logger.info(f"[AudioSocketHandler-TCP:Peer={self.peername}] Awaiting initial UUID frame from Asterisk...")
            header = await asyncio.wait_for(self.reader.readexactly(3), timeout=app_config.AUDIOSOCKET_READ_TIMEOUT_S)
            msg_type = header[0]
            payload_len = struct.unpack("!H", header[1:3])[0]

            if msg_type != TYPE_UUID:
                logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername}] Expected initial frame to be TYPE_UUID (0x01), but got type {msg_type:#04x}. Terminating.")
                return

            if payload_len != 16: # Standard UUID is 16 bytes
                logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername}] Received initial TYPE_UUID frame with unexpected payload length: {payload_len}. Expected 16 bytes. Terminating.")
                return

            payload_uuid_bytes = await asyncio.wait_for(self.reader.readexactly(payload_len), timeout=app_config.AUDIOSOCKET_READ_TIMEOUT_S / 2)

            try:
                self.asterisk_call_uuid = str(uuid.UUID(bytes=payload_uuid_bytes)) # This is the UUID from dialplan
            except ValueError:
                logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername}] Failed to parse received UUID payload: {payload_uuid_bytes.hex()}. Terminating.")
                return

            logger.info(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid}] Received initial Asterisk Dialplan UUID from first frame.")

            # --- Stage 2: Lookup internal call_id from database using the received Asterisk UUID ---
            loop = asyncio.get_running_loop()
            call_record = None
            MAX_DB_LOOKUP_ATTEMPTS = 3 # Try up to 3 times
            DB_LOOKUP_RETRY_DELAY_S = 0.2 # Wait 200ms between attempts
            for attempt in range(MAX_DB_LOOKUP_ATTEMPTS):
                logger.info(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid}] Attempting DB lookup for Asterisk UUID (Attempt {attempt + 1}/{MAX_DB_LOOKUP_ATTEMPTS})...")
                call_record = await loop.run_in_executor(None, db_manager.get_call_by_asterisk_uuid, self.asterisk_call_uuid)
                if call_record:
                    break # Found it
                if attempt < MAX_DB_LOOKUP_ATTEMPTS - 1:
                    logger.warning(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid}] Call record not found, retrying in {DB_LOOKUP_RETRY_DELAY_S}s...")
                    await asyncio.sleep(DB_LOOKUP_RETRY_DELAY_S)
                else: # Last attempt failed
                    logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid}] No active call record found for this Asterisk UUID after {MAX_DB_LOOKUP_ATTEMPTS} attempts. Terminating.")
                    return # Exit handle_frames

            if not call_record:
                logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid}] No active call record found for this Asterisk UUID. Terminating.")
                return

            self.call_id = call_record.id
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Successfully mapped Asterisk UUID to AppCallID. Starting main processing.")

            # --- Stage 3: Start background tasks and OpenAI initialization ---
            if self.call_id: # Redundant check, but safe
                # Start audio send task first to maintain connection
                audio_send_task = asyncio.create_task(self._send_audio_to_asterisk_task())
                logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Started audio send task")
                
                # Start Redis listener
                self._redis_listener_task = asyncio.create_task(self._listen_for_redis_commands())
                
                # Initialize OpenAI with retry
                max_retries = 3
                retry_delay = 1.0
                for attempt in range(max_retries):
                    try:
                        call_specific_prompt = "Default prompt"
                        if call_record.task_id:
                            task_record = await self.loop.run_in_executor(None, db_manager.get_task_by_id, call_record.task_id)
                            if task_record and task_record.generated_agent_prompt:
                                call_specific_prompt = task_record.generated_agent_prompt
                        
                        from audio_processing_service.openai_realtime_client import OpenAIRealtimeClient
                        self.openai_client = OpenAIRealtimeClient(
                            call_specific_prompt=call_specific_prompt,
                            openai_api_key=app_config.OPENAI_API_KEY,
                            loop=self.loop
                        )
                        conn_success = await self.openai_client.connect_and_initialize()
                        if conn_success:
                            self._openai_ready = True
                            await self._update_call_status_db(CallStatus.LIVE_AI_HANDLING)
                            
                            # Start OpenAI receive task
                            self._openai_receive_task = asyncio.create_task(self._listen_for_openai_responses())
                            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] OpenAI initialization successful, started receive task")
                            break
                        else:
                            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] OpenAI initialization failed, attempt {attempt + 1}/{max_retries}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delay)
                    except Exception as e:
                        logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Error during OpenAI initialization attempt {attempt + 1}: {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
            else:
                logger.error(f"[AudioSocketHandler-TCP:AstDialplanUUID={self.asterisk_call_uuid}] CRITICAL: AppCallID is None after UUID lookup. Cannot start tasks. Terminating.")
                return

            # Start main frame processing loop
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Starting main frame processing loop")

            # --- Main frame processing loop ---
            while not self._stop_event.is_set() and not self.reader.at_eof():
                try:
                    frame_header = await asyncio.wait_for(self.reader.readexactly(3), timeout=app_config.AUDIOSOCKET_READ_TIMEOUT_S)
                    frame_msg_type = frame_header[0]
                    frame_payload_len = struct.unpack("!H", frame_header[1:3])[0]
                    frame_payload = b''
                    if frame_payload_len > 0:
                        expected_duration_s = (frame_payload_len / 8000 / 2)
                        payload_read_timeout = 1.0 + (expected_duration_s * 2.0) + 1.0
                        frame_payload = await asyncio.wait_for(self.reader.readexactly(frame_payload_len), timeout=payload_read_timeout)

                    if frame_msg_type == TYPE_AUDIO:
                        if frame_payload:
                            logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received AUDIO frame, len={frame_payload_len}")
                            self._incoming_audio_frames.append(frame_payload) # Buffer the audio for saving
                            
                            # Store caller audio for recording (24kHz)
                            audio_np_8khz = np.frombuffer(frame_payload, dtype=np.int16)
                            audio_np_24khz = resample_audio(audio_np_8khz, AST_SAMPLE_RATE, OPENAI_SAMPLE_RATE)
                            
                            # Store in caller buffer for recording
                            async with self.session_lock:
                                self.session_caller_audio_buffer.append(audio_np_24khz.copy())
                            
                            # Process audio for OpenAI if ready
                            if self._openai_ready and self.openai_client and self.openai_client.is_connected:
                                if audio_np_24khz.size > 0:
                                    await self.openai_client.send_audio_chunk(audio_np_24khz.tobytes())
                                    logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Sent audio to OpenAI, len={frame_payload_len}")
                        else:
                            logger.warning(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received AUDIO frame with zero payload.")

                    elif frame_msg_type == TYPE_HANGUP:
                        logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received HANGUP frame from Asterisk.")
                        self._stop_event.set(); break

                    elif frame_msg_type == TYPE_DTMF:
                        dtmf_digit = frame_payload.decode('ascii', errors='replace') if frame_payload else '?'
                        logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received DTMF: {dtmf_digit}")
                        # TODO: Handle DTMF

                    elif frame_msg_type == TYPE_ERROR:
                        error_msg = frame_payload.decode('utf-8', errors='replace') if frame_payload else "Unknown error"
                        logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received ERROR frame: {error_msg}")

                    elif frame_msg_type == TYPE_UUID:
                        extra_uuid_payload_str = str(uuid.UUID(bytes=frame_payload)) if frame_payload_len == 16 else frame_payload.hex()
                        logger.warning(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received unexpected subsequent TYPE_UUID frame. Payload: {extra_uuid_payload_str}")

                    else: # Unknown frame type
                        logger.warning(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Received unknown frame type {frame_msg_type:#04x}, len={frame_payload_len}.")

                except asyncio.TimeoutError:
                    logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Frame read timeout, audio send task maintaining connection")
                    if self._stop_event.is_set(): break
                    continue
                except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError) as e:
                    logger.warning(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Connection error: {e}. Stopping handler.")
                    self._stop_event.set(); break
                except Exception as e:
                    logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Error processing subsequent frame: {e}", exc_info=True)
                    self._stop_event.set(); break

        except asyncio.TimeoutError:
            logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid or 'N/A'}] Timeout waiting for initial UUID frame from Asterisk. Terminating handler.")
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError) as e:
            logger.error(f"[AudioSocketHandler-TCP:Peer={self.peername},AstDialplanUUID={self.asterisk_call_uuid or 'N/A'}] Connection error during initial UUID read: {e}. Terminating handler.")
        except Exception as e:
            uuid_for_log = self.asterisk_call_uuid or "UUID_Not_Yet_Received"
            app_id_for_log = self.call_id or "AppCallID_Not_Yet_Mapped"
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] CRITICAL UNHANDLED ERROR in handle_frames: {e}", exc_info=True)
            if not self._stop_event.is_set(): self._stop_event.set()

        finally:
            # Save any buffered incoming audio before further cleanup
            await self._save_incoming_audio_as_wav()

            uuid_for_log = self.asterisk_call_uuid or "UUID_Unknown"
            app_id_for_log = self.call_id or "AppCallID_Unknown"

            logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] Frame handling loop ended for peer {self.peername}.")

            # Clean up OpenAI client if it exists
            if self.openai_client:
                try:
                    await self.openai_client.close()
                    logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log}] OpenAI client closed successfully")
                except Exception as e:
                    logger.error(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log}] Error closing OpenAI client: {e}")

            # Cancel all background tasks
            tasks_to_cancel = []
            if self._redis_listener_task and not self._redis_listener_task.done():
                tasks_to_cancel.append(('Redis listener', self._redis_listener_task))
            if audio_send_task and not audio_send_task.done():
                tasks_to_cancel.append(('Audio send', audio_send_task))
            if self._openai_receive_task and not self._openai_receive_task.done():
                tasks_to_cancel.append(('OpenAI receive', self._openai_receive_task))

            for task_name, task in tasks_to_cancel:
                logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log}] Cancelling {task_name} task")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log}] {task_name} task successfully cancelled")
                except Exception as e:
                    logger.error(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log}] Error awaiting cancelled {task_name} task: {e}")


            if self.writer and not self.writer.is_closing():
                logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] Closing writer to {self.peername}.")
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception as e_close:
                    logger.error(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] Error closing writer: {e_close}")

            if self.call_id: # Only attempt DB update if we successfully identified the call
                loop_final = asyncio.get_running_loop()
                current_call_status_obj = await loop_final.run_in_executor(None, db_manager.get_call_by_id, self.call_id)
                if current_call_status_obj and current_call_status_obj.status == CallStatus.LIVE_AI_HANDLING:
                     await self._update_call_status_db(CallStatus.COMPLETED_SYSTEM_HANGUP, call_conclusion="AudioSocket (TCP) disconnected or error during handling")

            logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] Cleanup complete for peer {self.peername}.")