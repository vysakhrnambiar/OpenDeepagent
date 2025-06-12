# audio_processing_service/audio_socket_handler.py
import asyncio
import struct
import sys
from pathlib import Path
from typing import Optional

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from common.data_models import RedisEndCallCommand # For example
from database import db_manager
from database.models import CallStatus

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

# Asterisk AudioSocket message types (from asty.py)
TYPE_HANGUP = 0x00
TYPE_UUID = 0x01 # Not expected from client after handshake
TYPE_DTMF = 0x03 # Not expected from client after handshake
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xff # Not expected from client after handshake

class AudioSocketHandler:
    def __init__(self,
                 reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 call_id: int,
                 redis_client: RedisClient,
                 peername: tuple | str | None):
        self.reader = reader
        self.writer = writer
        self.call_id = call_id
        self.redis_client = redis_client
        self.peername = peername if peername else "UnknownPeer"
        self._stop_event = asyncio.Event()
        self._redis_listener_task: Optional[asyncio.Task] = None
        # Placeholder for OpenAIRealtimeClient integration
        # self.openai_realtime_client = OpenAIRealtimeClient(call_id, ...)
        logger.info(f"[AudioSocketHandler:{self.call_id}] Initialized for peer {self.peername}")

    async def _update_call_status_db(self, status: CallStatus, **kwargs):
        """Helper to update call status in DB asynchronously."""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                db_manager.update_call_status,
                self.call_id,
                status,
                kwargs.get("hangup_cause"),
                kwargs.get("call_conclusion"),
                kwargs.get("duration_seconds"),
                kwargs.get("asterisk_channel"), # Might not be known here directly
                kwargs.get("call_uuid")          # Might not be known here directly
            )
            logger.info(f"[AudioSocketHandler:{self.call_id}] Call status updated to {status.value}")
        except Exception as e:
            logger.error(f"[AudioSocketHandler:{self.call_id}] Error updating call status to {status.value}: {e}", exc_info=True)

    async def _handle_redis_command(self, channel: str, command_data_dict: dict):
        logger.debug(f"[AudioSocketHandler:{self.call_id}] Received Redis command on {channel}: {command_data_dict}")
        command_type = command_data_dict.get("command_type")

        if command_type == RedisEndCallCommand.model_fields['command_type'].default:
            # This command is usually for CallAttemptHandler to send Hangup AMI.
            # If AudioSocketHandler needs to react directly (e.g., stop processing), it can.
            # For now, we'll assume CallAttemptHandler handles the actual hangup.
            # We might want to set stop_event here to terminate this handler.
            logger.info(f"[AudioSocketHandler:{self.call_id}] Received EndCall command via Redis. Signaling handler to stop.")
            self._stop_event.set()
        # Add other command handlers (e.g., sending audio from AI to Asterisk) if needed.

    async def _listen_for_redis_commands(self):
        # This handler might listen for commands from an AI service (e.g., AI generated audio to send to Asterisk)
        # Or commands from the CallAttemptHandler if direct coordination is needed.
        # For now, let's set up a basic listener for an "end_call" command for this handler itself.
        redis_channel_pattern = f"audiosocket_commands:{self.call_id}"
        logger.info(f"[AudioSocketHandler:{self.call_id}] Subscribing to Redis channel: {redis_channel_pattern}")
        try:
            await self.redis_client.subscribe_to_channel(redis_channel_pattern, self._handle_redis_command)
        except asyncio.CancelledError:
            logger.info(f"[AudioSocketHandler:{self.call_id}] Redis listener task cancelled.")
        except Exception as e:
            logger.error(f"[AudioSocketHandler:{self.call_id}] Error in Redis listener for {redis_channel_pattern}: {e}", exc_info=True)
            self._stop_event.set() # Stop main loop on critical Redis error
        finally:
            logger.info(f"[AudioSocketHandler:{self.call_id}] Redis listener stopped for {redis_channel_pattern}.")


    async def handle_frames(self):
        """Main loop to handle incoming frames from Asterisk."""
        logger.info(f"[AudioSocketHandler:{self.call_id}] Starting to handle frames from {self.peername}.")
        await self._update_call_status_db(CallStatus.LIVE_AI_HANDLING) # Or a more specific "AUDIO_PATH_ESTABLISHED"

        # Start listening for Redis commands relevant to this handler
        # self._redis_listener_task = asyncio.create_task(self._listen_for_redis_commands())

        # Placeholder: Initialize OpenAIRealtimeClient and start its processing
        # await self.openai_realtime_client.start_session()

        try:
            while not self._stop_event.is_set() and not self.reader.at_eof():
                try:
                    # Read frame header (type: 1 byte, length: 2 bytes network order)
                    header = await asyncio.wait_for(self.reader.readexactly(3), timeout=app_config.AUDIOSOCKET_READ_TIMEOUT_S)
                    msg_type = header[0]
                    payload_len = struct.unpack("!H", header[1:3])[0]

                    payload = b''
                    if payload_len > 0:
                        payload = await asyncio.wait_for(self.reader.readexactly(payload_len), timeout=app_config.AUDIOSOCKET_READ_TIMEOUT_S / 2)

                    if msg_type == TYPE_AUDIO:
                        if payload:
                            # logger.debug(f"[AudioSocketHandler:{self.call_id}] Received AUDIO frame, len={payload_len}")
                            # TODO: Process audio payload (e.g., send to OpenAIRealtimeClient)
                            # await self.openai_realtime_client.send_audio(payload)
                            pass # For now, just acknowledge receipt implicitly
                        else:
                            logger.warning(f"[AudioSocketHandler:{self.call_id}] Received AUDIO frame with zero payload length.")

                    elif msg_type == TYPE_HANGUP:
                        logger.info(f"[AudioSocketHandler:{self.call_id}] Received HANGUP frame from Asterisk.")
                        self._stop_event.set()
                        # The CallAttemptHandler will get a Hangup AMI event and update final status.
                        # This handler just needs to stop.
                        break
                    else:
                        logger.warning(f"[AudioSocketHandler:{self.call_id}] Received unknown frame type {msg_type} with payload length {payload_len}. Discarding.")

                except asyncio.TimeoutError:
                    logger.debug(f"[AudioSocketHandler:{self.call_id}] Timeout reading from Asterisk. Checking stop event.")
                    if self._stop_event.is_set():
                        break
                    continue # Continue loop if not stopping
                except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError) as e:
                    logger.warning(f"[AudioSocketHandler:{self.call_id}] Connection error with Asterisk: {e}. Stopping handler.")
                    self._stop_event.set()
                    break
                except Exception as e:
                    logger.error(f"[AudioSocketHandler:{self.call_id}] Error processing frame: {e}", exc_info=True)
                    self._stop_event.set()
                    break

        except Exception as e: # Catch errors in the main while setup
            logger.error(f"[AudioSocketHandler:{self.call_id}] Critical error in handle_frames main loop: {e}", exc_info=True)
            self._stop_event.set()
        finally:
            logger.info(f"[AudioSocketHandler:{self.call_id}] Frame handling loop ended for {self.peername}.")

            # Placeholder: Close OpenAIRealtimeClient session
            # if self.openai_realtime_client:
            #     await self.openai_realtime_client.close_session()

            # Cancel Redis listener task if running
            if self._redis_listener_task and not self._redis_listener_task.done():
                self._redis_listener_task.cancel()
                try:
                    await self._redis_listener_task
                except asyncio.CancelledError:
                    pass # Expected

            if self.writer and not self.writer.is_closing():
                logger.info(f"[AudioSocketHandler:{self.call_id}] Closing writer to {self.peername}.")
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception as e_close:
                    logger.error(f"[AudioSocketHandler:{self.call_id}] Error closing writer: {e_close}")

            # Update status to reflect that audio path has ended, if not already in a terminal state
            # Note: CallAttemptHandler should manage the final COMPLETED/FAILED status based on AMI events.
            # This handler might set an intermediate status like "AUDIO_PATH_CLOSED".
            current_call_status_obj = await asyncio.get_running_loop().run_in_executor(None, db_manager.get_call_by_id, self.call_id)
            if current_call_status_obj and current_call_status_obj.status == CallStatus.LIVE_AI_HANDLING:
                 await self._update_call_status_db(CallStatus.COMPLETED_SYSTEM_HANGUP, call_conclusion="AudioSocket disconnected")

            logger.info(f"[AudioSocketHandler:{self.call_id}] Cleanup complete for {self.peername}.")