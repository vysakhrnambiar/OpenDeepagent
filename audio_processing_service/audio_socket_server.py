# audio_processing_service/audio_socket_server.py
import asyncio
import sys
from pathlib import Path
# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---
from config.app_config import app_config
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from .audio_socket_handler import AudioSocketHandler
from common.data_models import RedisAIHandshakeCommand
from typing import Dict

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class AudioSocketServer:
    def __init__(self, host: str, port: int, redis_client: RedisClient):
        self.host = host
        self.port = port
        self.redis_client = redis_client
        self._server_task: asyncio.Task | None = None
        self._server: asyncio.AbstractServer | None = None
        self.active_handlers: Dict[str, AudioSocketHandler] = {}
        self._redis_listener_task: asyncio.Task | None = None
        logger.info(f"AudioSocketServer initialized to listen on {self.host}:{self.port}")

    def register_handler(self, handler: AudioSocketHandler):
        if handler.asterisk_call_uuid:
            self.active_handlers[handler.asterisk_call_uuid] = handler
            logger.info(f"[AudioSocketServer] Registered handler for UUID: {handler.asterisk_call_uuid}")

    def unregister_handler(self, handler: AudioSocketHandler):
        if handler.asterisk_call_uuid and handler.asterisk_call_uuid in self.active_handlers:
            del self.active_handlers[handler.asterisk_call_uuid]
            logger.info(f"[AudioSocketServer] Unregistered handler for UUID: {handler.asterisk_call_uuid}")

    async def _handle_server_redis_command(self, channel: str, command_data_dict: dict):
        logger.debug(f"[AudioSocketServer] Received Redis command on {channel}: {command_data_dict}")
        command_type = command_data_dict.get("command_type")
        if command_type == RedisAIHandshakeCommand.model_fields['command_type'].default:
            try:
                cmd = RedisAIHandshakeCommand(**command_data_dict)
                handler = self.active_handlers.get(cmd.asterisk_call_uuid)
                if handler:
                    logger.info(f"[AudioSocketServer] Routing TriggerAIResponse command to handler for UUID: {cmd.asterisk_call_uuid}")
                    await handler.trigger_ai_response()
                else:
                    logger.warning(f"[AudioSocketServer] No active handler found for UUID: {cmd.asterisk_call_uuid} to trigger AI response.")
            except Exception as e:
                logger.error(f"[AudioSocketServer] Error processing TriggerAIResponse command: {e}", exc_info=True)

    async def _listen_for_server_redis_commands(self):
        redis_channel_pattern = "audiosocket_server_commands:*"
        logger.info(f"[AudioSocketServer] Subscribing to Redis channel: {redis_channel_pattern}")
        try:
            await self.redis_client.subscribe_to_channel(redis_channel_pattern, self._handle_server_redis_command)
        except asyncio.CancelledError:
            logger.info("[AudioSocketServer] Redis listener task cancelled.")
        except Exception as e:
            logger.error(f"[AudioSocketServer] Critical error in Redis listener for {redis_channel_pattern}: {e}", exc_info=True)
        finally:
            logger.info("[AudioSocketServer] Redis listener stopped.")

    async def _handle_new_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peername = writer.get_extra_info('peername')
        logger.info(f"[AudioSocketServer-TCP] New TCP connection from {peername}")
        handler = None
        try:
            logger.info(f"[AudioSocketServer-TCP] Creating AudioSocketHandler for TCP connection from {peername}.")
            handler = AudioSocketHandler(
                reader=reader,
                writer=writer,
                redis_client=self.redis_client,
                peername=peername,
                server=self
            )
            logger.info(f"[AudioSocketServer-TCP] Handing off TCP connection from {peername} to handler.")
            await handler.handle_frames()
        except Exception as e:
            logger.error(f"[AudioSocketServer-TCP] CRITICAL UNHANDLED ERROR for connection from {peername}: {e}", exc_info=True)
        finally:
            if handler:
                self.unregister_handler(handler)
            if writer and not writer.is_closing():
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception as e_close:
                    logger.error(f"[AudioSocketServer-TCP] Error closing writer for peer {peername} in finally: {e_close}")
            logger.info(f"[AudioSocketServer-TCP] Finished handling/cleanup for connection from {peername}.")

    async def start(self):
        if self._server_task and not self._server_task.done():
            logger.warning("[AudioSocketServer] Server is already running.")
            return
        try:
            self._server = await asyncio.start_server(
                self._handle_new_connection,
                self.host,
                self.port
            )
            addr = self._server.sockets[0].getsockname()
            logger.info(f"[AudioSocketServer] Serving on {addr}")
            self._redis_listener_task = asyncio.create_task(self._listen_for_server_redis_commands())
        except Exception as e:
            logger.error(f"[AudioSocketServer] Failed to start server: {e}", exc_info=True)
            if self._server:
                self._server.close()
                await self._server.wait_closed()
            raise

    async def stop(self):
        if self._redis_listener_task and not self._redis_listener_task.done():
            self._redis_listener_task.cancel()
            try:
                await self._redis_listener_task
            except asyncio.CancelledError:
                pass
        if self._server:
            logger.info("[AudioSocketServer] Stopping server...")
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("[AudioSocketServer] Server stopped.")
        else:
            logger.info("[AudioSocketServer] Server not running or already stopped.")