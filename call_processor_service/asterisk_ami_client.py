# call_processor_service/asterisk_ami_client.py

import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, Callable, Awaitable, Optional, Set, Union # Added Union
import re 
from datetime import datetime
import uuid

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class AmiAction:
    def __init__(self, name: str, **kwargs):
        self.name = name
        self.headers = kwargs
        if 'ActionID' not in self.headers:
            # Ensure datetime and uuid are imported at the top of this file
            self.headers['ActionID'] = f"{name.lower()}-{datetime.now().timestamp()}-{uuid.uuid4().hex[:8]}"

    def __str__(self) -> str: # Type hint for clarity, though it returns bytes via encode
        message = f"Action: {self.name}\r\n"
        for key, value in self.headers.items():
            message += f"{key}: {value}\r\n"
        message += "\r\n"
        return message # Will be encoded before sending

    def encode(self) -> bytes:
        return str(self).encode('utf-8')


class AsteriskAmiClient:
    def __init__(self):
        self.host = app_config.ASTERISK_HOST
        self.port = app_config.ASTERISK_PORT
        self.username = app_config.ASTERISK_AMI_USER
        self.secret = app_config.ASTERISK_AMI_SECRET

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._lock = asyncio.Lock() 
        
        self._event_listeners: Dict[str, Set[Callable[[Dict[str, Any]], Awaitable[None]]]] = {}
        self._response_futures: Dict[str, asyncio.Future] = {}
        self._generic_event_listeners: Set[Callable[[Dict[str, Any]], Awaitable[None]]]] = set()

        self._listener_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._connection_retry_delay = 5 
        self._is_connecting = False # Flag to prevent multiple concurrent connect attempts

    async def _parse_ami_message(self, raw_data: bytes) -> Dict[str, Any]:
        message = {}
        try:
            # AMI messages are typically ISO-8859-1 or UTF-8. UTF-8 is safer.
            # Using 'ignore' for errors is a fallback.
            decoded_data = raw_data.decode('utf-8', errors='ignore')
            lines = decoded_data.strip().split('\r\n')
            for line in lines:
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    # Handle multi-line values for certain events (e.g., PeerStatus's Address)
                    # For now, simple key-value. If specific events need multi-line, this needs enhancement.
                    message[key.strip()] = value.strip()
            return message
        except Exception as e:
            logger.error(f"Error decoding/parsing AMI message: {e}. Raw data (first 100 bytes): {raw_data[:100]}")
            return {}


    async def _receive_loop(self):
        buffer = b""
        logger.info("AMI Receiver loop started.")
        try:
            while self._connected and self._reader:
                try:
                    data = await asyncio.wait_for(self._reader.read(4096), timeout=1.0) # Read up to 4KB
                    if not data: 
                        logger.warning("AMI connection closed by Asterisk (read no data).")
                        await self._handle_disconnect(trigger_reconnect=True)
                        break
                    
                    buffer += data
                    while b'\r\n\r\n' in buffer:
                        message_data, buffer = buffer.split(b'\r\n\r\n', 1)
                        message_dict = await self._parse_ami_message(message_data + b'\r\n\r\n')
                        
                        if not message_dict: continue

                        action_id = message_dict.get("ActionID")
                        if "Response" in message_dict:
                            if action_id and action_id in self._response_futures:
                                future = self._response_futures.pop(action_id)
                                if not future.done(): future.set_result(message_dict)
                            # Handle "Response: Follows" for multi-part responses (e.g., CoreShowChannels)
                            elif message_dict["Response"] == "Follows" and action_id and action_id in self._response_futures:
                                # This is a multi-part response. The future should expect a list.
                                # This requires more complex logic to accumulate '--END COMMAND--'
                                # For now, we'll just log it. Simple send_action expects single response.
                                logger.debug(f"Multi-part AMI response started for ActionID {action_id}: {message_dict}")
                                # The future for ActionID should ideally collect all parts.
                                # This simplified client doesn't fully handle multi-event responses correctly yet.
                                # It will likely only resolve the future with the first "Response: Success/Error"
                                # or the final one if "--END COMMAND--" isn't handled by "Event" logic.
                            else:
                                logger.warning(f"Received response for unknown/unhandled ActionID: {action_id} - {message_dict.get('Response')}")
                        elif "Event" in message_dict:
                            event_name = message_dict["Event"]
                            # Check if this event is the end of a multi-part response
                            if event_name.endswith("Complete") or (message_dict.get("EventList") == "Complete"): # Common patterns
                                if action_id and action_id in self._response_futures:
                                    # This might be the completion event for an action that returned "Response: Follows"
                                    # For now, we don't automatically resolve the future here.
                                    # A more robust client would collect all events until this completion marker.
                                    logger.debug(f"Completion event for ActionID {action_id}: {event_name}")

                            # Standard event dispatching
                            if event_name in self._event_listeners:
                                for callback in list(self._event_listeners[event_name]):
                                    asyncio.create_task(callback(message_dict))
                            for g_callback in list(self.generic_event_listeners):
                                asyncio.create_task(g_callback(message_dict))
                        else:
                            logger.warning(f"Unknown AMI message type (no Response/Event): {message_dict}")
                
                except asyncio.TimeoutError: continue
                except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError) as e:
                    logger.warning(f"AMI connection error in receive loop: {e}.")
                    await self._handle_disconnect(trigger_reconnect=True)
                    break 
                except Exception as e:
                    logger.error(f"Unexpected error in AMI receive loop: {e}", exc_info=True)
                    await self._handle_disconnect(trigger_reconnect=True)
                    break
        finally:
            logger.info("AMI Receiver loop stopped.")

    async def _send_keepalive(self):
        while self._connected:
            try:
                await asyncio.sleep(20) 
                if self._connected:
                    logger.debug("Sending AMI Ping")
                    response = await self.send_action("Ping", timeout=5) # Ping with shorter timeout
                    if not response or response.get("Response") != "Success":
                        logger.warning(f"AMI Ping failed or bad response: {response}. Connection might be stale.")
                        # If ping fails, explicitly trigger disconnect and reconnect
                        await self._handle_disconnect(trigger_reconnect=True)
                        break # Exit keepalive, connect_and_login will retry
            except asyncio.CancelledError:
                logger.info("AMI Keepalive task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in AMI keepalive task: {e}")
                await asyncio.sleep(5)

    async def _handle_disconnect(self, trigger_reconnect=False):
        if not self._connected and not self._is_connecting: # Already disconnected or not trying to connect
            return

        logger.warning("AMI client disconnected or handling disconnect.")
        self._connected = False
        
        if self._writer:
            self._writer.close()
            try: await self._writer.wait_closed()
            except Exception: pass
        self._writer = None
        self._reader = None

        if self._listener_task and not self._listener_task.done(): self._listener_task.cancel()
        if self._keepalive_task and not self._keepalive_task.done(): self._keepalive_task.cancel()
        
        for future_id, future in list(self._response_futures.items()): # Iterate over a copy
            if not future.done():
                future.set_exception(ConnectionError("AMI client disconnected"))
            self._response_futures.pop(future_id, None)

        if trigger_reconnect and not self._is_connecting:
            logger.info("Scheduling AMI reconnection attempt.")
            asyncio.create_task(self.connect_and_login()) # Non-blocking call to reconnect


    async def connect_and_login(self) -> bool:
        # Prevent re-entrant calls to connect_and_login
        if self._is_connecting:
            logger.debug("AMI connection attempt already in progress.")
            return False # Or await a connection event if multiple callers need to wait
        
        self._is_connecting = True

        try:
            async with self._lock: # Lock to serialize connection attempts
                if self._connected:
                    self._is_connecting = False
                    return True

                # Explicitly clean up any previous stale connection resources before new attempt
                if self._writer: self._writer.close(); await self._writer.wait_closed()
                self._writer = None; self._reader = None

                logger.info(f"Attempting to connect to AMI: {self.host}:{self.port}")
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=10 # Connection timeout
                )
                
                # More robust banner reading: read line by line until empty line or timeout
                banner_lines = []
                try:
                    while True:
                        line_bytes = await asyncio.wait_for(self._reader.readuntil(b'\r\n'), timeout=2.0)
                        if line_bytes == b'\r\n': # Empty line after banner (sometimes happens)
                            break
                        line_str = line_bytes.decode('utf-8', errors='ignore').strip()
                        if not line_str: # Should not happen if readuntil finds \r\n
                            break
                        banner_lines.append(line_str)
                        if "Asterisk Call Manager" in line_str: # Typical banner line
                            # It's possible the banner is multi-line before an empty line separator
                            # But usually, it's just one line. Stop after finding it.
                            break 
                except asyncio.TimeoutError:
                    if not banner_lines:
                        logger.error("Timeout reading AMI banner.")
                        await self._handle_disconnect()
                        self._is_connecting = False
                        return False
                except (asyncio.IncompleteReadError, ConnectionError) as e:
                    logger.error(f"Error reading AMI banner: {e}")
                    await self._handle_disconnect()
                    self._is_connecting = False
                    return False

                if banner_lines:
                    logger.info(f"AMI Banner: {' | '.join(banner_lines)}")
                else:
                    logger.warning("No clear AMI banner received, proceeding with login attempt.")

                # At this point, TCP connection is established.
                logger.critical(f"AMI CLIENT DEBUG (Pre-Login): Username='{self.username}', Secret='{self.secret}'")
                login_action = AmiAction("Login", Username=self.username, Secret=self.secret, Events="call,system,user,agent") # Added agent events
                
                logger.info(f"Sending AMI Login action for user {self.username} with ActionID: {login_action.headers['ActionID']}")
                
                # Send login action
                if not self._writer: # Should not happen if connection succeeded
                    logger.error("AMI writer is None after connection. Cannot send Login.")
                    await self._handle_disconnect()
                    self._is_connecting = False
                    return False

                self._writer.write(login_action.encode())
                await self._writer.drain()
                logger.debug("Login action written and drained.")

                # Now, specifically wait for the response to THIS ActionID
                # The _receive_loop is not started yet. We need to read the login response directly here.
                login_response_dict = None
                response_buffer = b""
                try:
                    while True: # Loop to read until we get a full message block for the response
                        chunk = await asyncio.wait_for(self._reader.read(4096), timeout=10.0) # Read login response
                        if not chunk:
                            logger.error("AMI connection closed while waiting for Login response.")
                            await self._handle_disconnect()
                            self._is_connecting = False
                            return False
                        response_buffer += chunk
                        if b'\r\n\r\n' in response_buffer:
                            message_data, response_buffer = response_buffer.split(b'\r\n\r\n', 1)
                            login_response_dict = await self._parse_ami_message(message_data + b'\r\n\r\n')
                            # Check if this is the response to our Login action
                            if login_response_dict.get("ActionID") == login_action.headers['ActionID']:
                                logger.info(f"Received direct response for Login ActionID {login_action.headers['ActionID']}: {login_response_dict}")
                                break # Got our response
                            else:
                                logger.warning(f"Received unexpected message while waiting for Login response: {login_response_dict}. Buffering: {response_buffer!r}")
                                # This could be an event. If so, we might need to handle it or log and continue waiting for our ActionID.
                                # For now, we assume the first full message is the login response or an error.
                                if "Response" not in login_response_dict: # if it's an event
                                    logger.info(f"Message {login_response_dict.get('Event')} received before login response, ignoring for now.")
                                    login_response_dict = None # Discard, wait for actual response
                                else: # It's a response, but not for our ActionID? Unlikely for login.
                                      break # Process this response
                except asyncio.TimeoutError:
                    logger.error(f"Timeout waiting for Login response from AMI for ActionID {login_action.headers['ActionID']}.")
                    await self._handle_disconnect()
                    self._is_connecting = False
                    return False
                
                if login_response_dict and login_response_dict.get("Response") == "Success":
                    self._connected = True # Officially connected and logged in
                    logger.info("AMI Login Successful.")
                    
                    if self._listener_task and not self._listener_task.done(): self._listener_task.cancel()
                    self._listener_task = asyncio.create_task(self._receive_loop())
                    
                    if self._keepalive_task and not self._keepalive_task.done(): self._keepalive_task.cancel()
                    self._keepalive_task = asyncio.create_task(self._send_keepalive())
                    self._is_connecting = False
                    return True
                else:
                    logger.error(f"AMI Login Failed. Response: {login_response_dict}")
                    await self._handle_disconnect()
                    self._is_connecting = False
                    return False

        except ConnectionRefusedError:
            logger.error(f"AMI connection refused to {self.host}:{self.port}.")
        except asyncio.TimeoutError:
             logger.error(f"AMI connection to {self.host}:{self.port} timed out during connect/login process.")
        except Exception as e:
            logger.error(f"General failure during AMI connect_and_login: {e}", exc_info=True)
        
        await self._handle_disconnect() # Ensure cleanup on any failure path
        self._is_connecting = False
        # No retry loop here; let higher-level logic or periodic checks trigger reconnects
        # by calling connect_and_login() again if needed.
        # Or, if a service depends on it, it can retry calling this.
        # For immediate retry, add the while True loop back here with a sleep.
        # For now, it will attempt one full connection/login sequence.
        return False


    async def send_action(self, action: Union[str, AmiAction], timeout: float = 10.0, **kwargs) -> Optional[Dict[str, Any]]:
        if not self._connected:
            logger.warning("AMI not connected. Attempting to connect before sending action.")
            if not await self.connect_and_login():
                logger.error("Failed to connect to AMI. Cannot send action.")
                return {"Response": "Error", "Message": "AMI connection failed"}

        if not self._connected or not self._writer: # Re-check after connect attempt
             logger.error("Still not connected to AMI after attempt. Cannot send action.")
             return {"Response": "Error", "Message": "AMI still not connected"}

        action_obj = action if isinstance(action, AmiAction) else AmiAction(action, **kwargs)
        action_id = action_obj.headers['ActionID']
        future = asyncio.get_event_loop().create_future()
        self._response_futures[action_id] = future

        logger.debug(f"Sending AMI Action: {action_obj.name}, ActionID: {action_id}, Headers: {action_obj.headers}")
        try:
            async with self._lock: # Lock for writing, though connect_and_login also uses it.
                                 # A finer-grained lock for just _writer might be better if connect_and_login is slow.
                if not self._writer: 
                    logger.error("AMI writer is None before sending. Aborting action.")
                    if action_id in self._response_futures: self._response_futures.pop(action_id)
                    future.set_exception(ConnectionError("AMI writer is None"))
                    # Attempt to reconnect
                    await self._handle_disconnect(trigger_reconnect=True)
                    return {"Response": "Error", "Message": "AMI writer was None"}

                self._writer.write(action_obj.encode())
                await self._writer.drain()
            
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for response to ActionID {action_id} ({action_obj.name})")
        except (ConnectionResetError, BrokenPipeError) as e:
            logger.error(f"Connection error sending AMI action {action_id} ({action_obj.name}): {e}")
            await self._handle_disconnect(trigger_reconnect=True)
        except Exception as e:
            logger.error(f"Error sending AMI action {action_id} ({action_obj.name}): {e}", exc_info=True)
        finally: # Ensure future is removed
            if action_id in self._response_futures:
                # If future not done, it means an error occurred before response was set
                popped_future = self._response_futures.pop(action_id)
                if not popped_future.done():
                    popped_future.set_result({"Response": "Error", "Message": "Action failed or timed out before response"})
        return {"Response": "Error", "Message": "Action failed or timed out"}


    def add_event_listener(self, event_name: str, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        if event_name not in self._event_listeners: self._event_listeners[event_name] = set()
        self._event_listeners[event_name].add(callback)
        logger.debug(f"Added listener for AMI event: {event_name}")

    def remove_event_listener(self, event_name: str, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        if event_name in self._event_listeners:
            self._event_listeners[event_name].discard(callback)
            if not self._event_listeners[event_name]: del self._event_listeners[event_name]
        logger.debug(f"Removed listener for AMI event: {event_name}")

    def add_generic_event_listener(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        self.generic_event_listeners.add(callback)
        logger.debug("Added generic AMI event listener.")

    def remove_generic_event_listener(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        self.generic_event_listeners.discard(callback)
        logger.debug("Removed generic AMI event listener.")

    async def close(self):
        logger.info("Closing AMI client connection.")
        if self._keepalive_task and not self._keepalive_task.done(): self._keepalive_task.cancel()
        if self._listener_task and not self._listener_task.done(): self._listener_task.cancel()
        
        # Using a new lock for close to avoid deadlocks if _lock is held by connect_and_login
        async with asyncio.Lock(): 
            await self._handle_disconnect(trigger_reconnect=False) # Don't trigger reconnect on explicit close

        try:
            if self._keepalive_task: await self._keepalive_task
        except asyncio.CancelledError: pass
        try:
            if self._listener_task: await self._listener_task
        except asyncio.CancelledError: pass

        self._event_listeners.clear()
        self.generic_event_listeners.clear()
        logger.info("AMI client closed.")

if __name__ == "__main__": # pragma: no cover
    async def main_ami_test():
        # ... (main_ami_test from previous version, adjusted to call the new connect_and_login)
        if not (app_config.ASTERISK_AMI_USER and app_config.ASTERISK_AMI_SECRET):
            logger.error("AMI Username or Secret not configured. Aborting test.")
            return

        ami_client = AsteriskAmiClient()
        
        async def example_event_handler(event: Dict[str, Any]):
            if event.get("Event") == "Hangup":
                logger.info(f"[EXAMPLE_HANDLER] Hangup: {event.get('Channel')} cause: {event.get('Cause-txt')}")
            elif event.get("Event") == "Newchannel":
                 logger.info(f"[EXAMPLE_HANDLER] Newchannel: {event.get('Channel')} uniqueid: {event.get('UniqueID')}")

        ami_client.add_generic_event_listener(example_event_handler)

        logger.info("Attempting initial AMI connect and login...")
        connected = await ami_client.connect_and_login() # Call the revised method
        
        if not connected:
            logger.error("Failed to connect to AMI for test after initial attempt. Exiting.")
            return

        logger.info("AMI Client connected for test.")
        
        endpoints_response = await ami_client.send_action("PJSIPShowEndpoints")
        if endpoints_response:
            logger.info(f"PJSIPShowEndpoints Response: {endpoints_response.get('Response')}, Message: {endpoints_response.get('Message')}")
            if endpoints_response.get("Response") == "Follows":
                 logger.info("PJSIPShowEndpoints returns multiple events. Full list not captured by this simple test.")

        logger.info("Listening for AMI events for 30 seconds...")
        await asyncio.sleep(30)

        await ami_client.close()
        logger.info("AMI Client test finished.")

    # asyncio.run(main_ami_test())
    pass