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

    async def _handle_new_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """
        Callback for new asyncio stream connections.
        This will delegate to AudioSocketHandler.
        """
        peername = writer.get_extra_info('peername')
        logger.info(f"[AudioSocketServer] New connection from {peername}")

        # --- Extract call_id from the WebSocket request path ---
        # This part is tricky with raw asyncio streams as there's no direct HTTP upgrade handshake visible here.
        # Asterisk's AudioSocket (module res_rtp_asterisk/rtp_engine.c->ast_rtp_audiosocket_connect)
        # effectively makes a WebSocket connection. The path is part of the URI given in Originate's Data.
        #
        # For raw TCP/asyncio.start_server, the path isn't directly available in the reader/writer objects
        # in the same way it is for a full HTTP/WebSocket server library like 'websockets' or FastAPI.
        #
        # Asterisk's audiosocket.c sends a "GET <path> HTTP/1.1" handshake.
        # We need to read this initial handshake to extract the path.

        http_request_line_bytes = b''
        try:
            # Read the first line of the HTTP request (e.g., "GET /callaudio/123 HTTP/1.1\r\n")
            http_request_line_bytes = await asyncio.wait_for(reader.readuntil(b'\r\n'), timeout=5.0)
            http_request_line = http_request_line_bytes.decode('utf-8').strip()
            logger.debug(f"[AudioSocketServer] Received initial HTTP line: {http_request_line}")

            # Read and discard headers until an empty line is found
            while True:
                header_line_bytes = await asyncio.wait_for(reader.readuntil(b'\r\n'), timeout=2.0)
                if header_line_bytes == b'\r\n': # Empty line indicates end of headers
                    break
                logger.debug(f"[AudioSocketServer] Discarding header: {header_line_bytes.decode('utf-8').strip()}")

            parts = http_request_line.split()
            if len(parts) < 2 or not parts[0] == "GET":
                logger.error(f"[AudioSocketServer] Invalid HTTP request line from {peername}: {http_request_line}")
                writer.close()
                await writer.wait_closed()
                return

            path = parts[1] # e.g., /callaudio/123
            path_segments = path.strip("/").split("/") # e.g., ['callaudio', '123']

            if len(path_segments) < 2 or path_segments[0] != "callaudio":
                logger.error(f"[AudioSocketServer] Invalid path from {peername}: {path}. Expected /callaudio/<call_id>")
                # Send a basic HTTP error response before closing
                error_response = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                writer.write(error_response)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            call_id_str = path_segments[-1]
            try:
                call_id = int(call_id_str)
            except ValueError:
                logger.error(f"[AudioSocketServer] Could not parse call_id '{call_id_str}' from path {path} for {peername}")
                error_response = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                writer.write(error_response)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            logger.info(f"[AudioSocketServer] Extracted Call ID: {call_id} for connection from {peername}")

            # Send WebSocket upgrade success response (minimal for Asterisk AudioSocket)
            # AudioSocket expects a 101 Switching Protocols, but doesn't strictly validate all headers
            # like a full browser would. The key is that after this, it expects binary frames.
            # From Asterisk's res_rtp_audiosocket.c, it looks for "HTTP/1.1 101"
            ws_accept_response = (
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                # b"Sec-WebSocket-Accept: ... \r\n" # AudioSocket doesn't seem to require/validate Sec-WebSocket-Accept
                b"\r\n"
            )
            writer.write(ws_accept_response)
            await writer.drain()
            logger.info(f"[AudioSocketServer] Sent WebSocket handshake response to {peername} for Call ID {call_id}")

            # Now that handshake is "complete", create and run the handler
            handler = AudioSocketHandler(reader, writer, call_id, self.redis_client, peername)
            await handler.handle_frames() # This is the main loop for the handler

        except asyncio.TimeoutError:
            logger.warning(f"[AudioSocketServer] Timeout during handshake with {peername if peername else 'unknown client'}.")
            if writer and not writer.is_closing():
                writer.close()
                await writer.wait_closed()
        except ConnectionResetError:
            logger.warning(f"[AudioSocketServer] Connection reset by peer {peername if peername else 'unknown client'} during handshake or handling.")
        except Exception as e:
            logger.error(f"[AudioSocketServer] Error handling new connection from {peername if peername else 'unknown client'}: {e}", exc_info=True)
            if writer and not writer.is_closing(): # Ensure writer is closed on error
                writer.close()
                await writer.wait_closed()

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
            # Keep server reference to close it later
            # self._server = server # Already done by assignment above

            addr = self._server.sockets[0].getsockname()
            logger.info(f"[AudioSocketServer] Serving on {addr}")

            # Keep the server running in the background
            # The server object itself handles incoming connections in its own loop.
            # We don't need to `await server.serve_forever()` if this `start` method
            # is called as part of a larger asyncio application structure (e.g., in main.py).
            # If `start` is meant to be blocking, then `serve_forever` would be used.
            # For non-blocking, we just need to ensure the server object is kept alive.
            # Storing it in self._server is sufficient if this AudioSocketServer instance is kept alive.

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

if __name__ == '__main__': # pragma: no cover
    # Basic test for the server
    async def main_test():
        # Mock RedisClient for testing if needed, or use a real one if configured
        class MockRedisClient:
            async def publish_command(self, channel, command):
                print(f"MockRedis: Published to {channel}: {command}")
                return True
            async def subscribe_to_channel(self, pattern, callback):
                print(f"MockRedis: Subscribed to {pattern}")
                # Simulate receiving a command after a delay
                await asyncio.sleep(10)
                # test_cmd_data = RedisEndCallCommand(call_attempt_id=123, reason="Test hangup").model_dump()
                # await callback(f"call_commands:123", test_cmd_data)

        mock_redis = MockRedisClient()
        server = AudioSocketServer(
            host=app_config.AUDIOSOCKET_HOST,
            port=app_config.AUDIOSOCKET_PORT,
            redis_client=mock_redis # type: ignore
        )
        await server.start()

        # Keep server running for a while for manual testing
        # (e.g., connect with `telnet localhost 1200` then type `GET /callaudio/123 HTTP/1.1` and headers)
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Test server shutting down...")
        finally:
            await server.stop()

    try:
        asyncio.run(main_test())
    except KeyboardInterrupt:
        pass