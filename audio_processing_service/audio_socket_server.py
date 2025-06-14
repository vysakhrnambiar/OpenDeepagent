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
from common.redis_client import RedisClient # If needed by handler later
from .audio_socket_handler import AudioSocketHandler

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class AudioSocketServer:
    def __init__(self, host: str, port: int, redis_client: RedisClient):
        self.host = host
        self.port = port
        self.redis_client = redis_client # Will be passed to handler
        self._server_task: asyncio.Task | None = None
        self._server: asyncio.AbstractServer | None = None
        logger.info(f"AudioSocketServer initialized to listen on {self.host}:{self.port}")

       # In class AudioSocketServer:
    async def _handle_new_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peername = writer.get_extra_info('peername')
        logger.info(f"[AudioSocketServer-TCP] New TCP connection from {peername}")
        
        # For raw TCP, we don't get the UUID from an HTTP path.
        # The AudioSocketHandler will be responsible for reading the initial TYPE_UUID frame
        # from Asterisk to get the asterisk_call_uuid.

        # We still need a way to pass the RedisClient to the handler.
        # The handler will determine the call_id and asterisk_call_uuid after reading the first frame.

        try:
            logger.info(f"[AudioSocketServer-TCP] Creating AudioSocketHandler for TCP connection from {peername}.")
            # Pass reader, writer, and redis_client.
            # The handler will resolve call_id and asterisk_call_uuid itself.
            handler = AudioSocketHandler(
                reader=reader, 
                writer=writer, 
                redis_client=self.redis_client, 
                peername=peername
                # call_id and asterisk_call_uuid will be determined by the handler
            )
            logger.info(f"[AudioSocketServer-TCP] Handing off TCP connection from {peername} to handler.")
            await handler.handle_frames()

        except ConnectionResetError:
            logger.warning(f"[AudioSocketServer-TCP] Connection reset by peer {peername} during initial handling or handler execution.")
        except BrokenPipeError:
             logger.warning(f"[AudioSocketServer-TCP] Broken pipe with peer {peername}, likely client closed connection abruptly.")
        except Exception as e:
            logger.error(f"[AudioSocketServer-TCP] CRITICAL UNHANDLED ERROR for connection from {peername}: {e}", exc_info=True)
        finally:
            if writer and not writer.is_closing(): # Ensure writer is closed if an error occurred before handler took full control or if handler failed early
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
            
        except Exception as e:
            logger.error(f"[AudioSocketServer] Failed to start server: {e}", exc_info=True)
            if self._server:
                self._server.close()
                await self._server.wait_closed()
            raise

    async def stop(self):
        if self._server:
            logger.info("[AudioSocketServer] Stopping server...")
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("[AudioSocketServer] Server stopped.")
        else:
            logger.info("[AudioSocketServer] Server not running or already stopped.")