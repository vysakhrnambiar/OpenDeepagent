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

# Asterisk AudioSocket message types (for TCP protocol)
TYPE_HANGUP = 0x00
TYPE_UUID = 0x01   # Asterisk sends the Dialplan-provided UUID in the first frame with this type.
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xff

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
        logger.info(f"[AudioSocketHandler-TCP:Peer={self.peername}] Initialized, awaiting initial UUID frame from Asterisk.")

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


    async def _save_incoming_audio_as_wav(self):
        """Save collected audio frames as a WAV file.
        Audio format is PCM16 at 8000Hz sample rate."""
        if not self._incoming_audio_frames:
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] No audio frames to save.")
            return

        # Create recordings directory if it doesn't exist
        recordings_dir = Path(_project_root) / "recordings"
        recordings_dir.mkdir(exist_ok=True)

        # Generate filename with call identification
        timestamp = asyncio.get_running_loop().time()
        filename = f"call_{self.call_id}_{self.asterisk_call_uuid}_{int(timestamp)}.wav"
        wav_path = recordings_dir / filename

        try:
            # Concatenate all audio frames
            audio_data = b''.join(self._incoming_audio_frames)
            
            # Write WAV file
            with wave.open(str(wav_path), 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono audio
                wav_file.setsampwidth(2)  # 2 bytes per sample (16-bit PCM)
                wav_file.setframerate(8000)  # 8kHz sample rate
                wav_file.writeframes(audio_data)

            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Saved audio to {wav_path}")
        except Exception as e:
            logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Error saving audio file: {e}", exc_info=True)

    async def handle_frames(self):
        """Main loop to handle incoming frames from Asterisk (TCP AudioSocket protocol)."""
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

            # --- Stage 3: Proceed with normal operation ---
            await self._update_call_status_db(CallStatus.LIVE_AI_HANDLING)

            if self.call_id: # Redundant check, but safe
                 self._redis_listener_task = asyncio.create_task(self._listen_for_redis_commands())
            else:
                logger.error(f"[AudioSocketHandler-TCP:AstDialplanUUID={self.asterisk_call_uuid}] CRITICAL: AppCallID is None after UUID lookup. Cannot start Redis listener. Terminating.")
                return

            # ---- TEST: Python sends audio to Asterisk ----
            logger.critical(f"PYTHON IS ABOUT TO SEND TEST TONE NOW for AppCallID={self.call_id}")
            logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] TEST: Attempting to send test TONE to Asterisk.")
            try:
                sample_rate = 8000
                frequency = 440  # A4 note
                duration_ms_per_chunk = 20
                num_samples_per_chunk = int(sample_rate * duration_ms_per_chunk / 1000)
                num_test_frames = 50 # Send for 1 second

                t = np.linspace(0, duration_ms_per_chunk / 1000, num_samples_per_chunk, endpoint=False)
                sine_wave_chunk_float = 0.3 * np.sin(2 * np.pi * frequency * t)
                sine_wave_chunk_pcm16 = (sine_wave_chunk_float * 32767).astype(np.int16)
                tone_payload_chunk = sine_wave_chunk_pcm16.tobytes()

                if len(tone_payload_chunk) < 320:
                    logger.warning(f"Tone payload chunk size is {len(tone_payload_chunk)}, expected 320. Padding.")
                    tone_payload_chunk = tone_payload_chunk + b'\x00' * (320 - len(tone_payload_chunk))
                elif len(tone_payload_chunk) > 320:
                    logger.warning(f"Tone payload chunk size is {len(tone_payload_chunk)}, expected 320. Truncating.")
                    tone_payload_chunk = tone_payload_chunk[:320]

                test_audio_header = struct.pack("!BH", TYPE_AUDIO, len(tone_payload_chunk))

                for i in range(num_test_frames):
                    if self._stop_event.is_set(): # Check stop event before sending
                        logger.warning(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Stop event set during test TONE send. Aborting.")
                        break
                    if self.writer and not self.writer.is_closing():
                        self.writer.write(test_audio_header + tone_payload_chunk)
                        await self.writer.drain()
                        logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Sent test TONE frame {i+1}/{num_test_frames} to Asterisk.")
                        await asyncio.sleep(0.015)  # Match the echo timing
                    else:
                        logger.warning(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Writer closed during test TONE send. Aborting.")
                        break
                if not self._stop_event.is_set(): # Log finish only if not stopped
                    logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] TEST: Finished sending test TONE.")
            except Exception as e_send_test:
                logger.error(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] TEST: Error sending test TONE: {e_send_test}", exc_info=True)
            # ---- END TEST ----

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
                            
                            # Echo back the audio frame immediately
                            if self.writer and not self.writer.is_closing():
                                echo_header = struct.pack("!BH", TYPE_AUDIO, len(frame_payload))
                                self.writer.write(echo_header + frame_payload)
                                await self.writer.drain()
                                logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id}] Echoed back AUDIO frame, len={frame_payload_len}")
                                await asyncio.sleep(0.015)  # Small delay for frame synchronization
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
                    logger.debug(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Timeout reading frame. Checking stop event.")
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

            if self._redis_listener_task and not self._redis_listener_task.done():
                logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] Cancelling Redis listener task.")
                self._redis_listener_task.cancel()
                try:
                    await self._redis_listener_task
                except asyncio.CancelledError:
                    logger.info(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log},AstDialplanUUID={uuid_for_log}] Redis listener task successfully cancelled.")
                except Exception as e_redis_cancel_await: # Catch any other errors during await
                    logger.error(f"[AudioSocketHandler-TCP:AppCallID={app_id_for_log}] Error awaiting cancelled Redis listener: {e_redis_cancel_await}")


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