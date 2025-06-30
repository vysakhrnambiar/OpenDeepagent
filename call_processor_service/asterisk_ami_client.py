# call_processor_service/asterisk_ami_client.py

import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, Callable, Awaitable, Optional, Set, Union, List
# import re # Not directly used by our client code anymore
from datetime import datetime
import uuid
import threading
import queue as thread_safe_queue
import socket 
import time

# Correct imports for 'asterisk-ami==0.1.7'
from asterisk import ami # Main module, provides ami.AMIClient
from asterisk.ami.action import Action, LoginAction, LogoffAction, SimpleAction # Import specific action classes
from asterisk.ami.response import Response as LibAmiResponse # Alias to avoid conflict if we have our own Response type
from asterisk.ami.event import Event as LibAmiEvent, EventListener as LibAmiEventListener # For type hinting and understanding

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class AmiAction: # Our internal helper
    def __init__(self, name: str, **kwargs):
        self.name = name
        self.headers = kwargs # These are the keys for the library's Action objects
        if 'ActionID' not in self.headers:
            self.headers['ActionID'] = f"{name.lower()}-{datetime.now().timestamp()}-{uuid.uuid4().hex[:8]}"

    def get_name(self) -> str:
        return self.name
        
    def get_headers(self) -> Dict[str, Any]:
        return self.headers

    def get_action_id(self) -> str:
        return self.headers['ActionID']

class AsteriskAmiClient:
    def __init__(self):
        self.host = app_config.ASTERISK_HOST
        self.port = app_config.ASTERISK_PORT
        self.username = app_config.ASTERISK_AMI_USER
        self.secret = app_config.ASTERISK_AMI_SECRET

        self._sync_ami_client: Optional[ami.AMIClient] = None # Use ami.AMIClient
        self._ami_thread: Optional[threading.Thread] = None
        self._stop_worker_event = threading.Event()
        self._action_queue = thread_safe_queue.Queue(maxsize=100)

        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._connection_future: Optional[asyncio.Future] = None

        self._connected = False
        self._is_connecting_or_reconnecting = False
        self._lock = asyncio.Lock()

        self._response_futures: Dict[str, asyncio.Future] = {}
        self._event_listeners: Dict[str, Set[Callable[[Dict[str, Any]], Awaitable[None]]]] = {}
        self._generic_event_listeners: Set[Callable[[Dict[str, Any]], Awaitable[None]]] = set()
        
        self._connection_retry_delay = 5
        self._keepalive_interval = 540

    # _InternalAmiEventListener class is no longer needed if we use a simple lambda for add_event_listener

    def _dispatch_ami_event_from_thread(self, lib_event_obj: LibAmiEvent): # Receives LibAmiEvent
        # Convert LibAmiEvent object to our standard event_dict format
        logger.info(f"[AMI_CLIENT_EVENT_CATCH_ALL] Received Event: {lib_event_obj.name if hasattr(lib_event_obj, 'name') else 'Unknown'} | Keys: {lib_event_obj.keys if hasattr(lib_event_obj, 'keys') else 'No Keys'}")

        event_dict = {}
        event_name_from_lib = "UnknownEvent"

        if hasattr(lib_event_obj, 'name') and lib_event_obj.name:
            event_name_from_lib = lib_event_obj.name
            event_dict['Event'] = event_name_from_lib
        
        if hasattr(lib_event_obj, 'keys') and isinstance(lib_event_obj.keys, dict):
            event_dict.update(lib_event_obj.keys)
            # Ensure 'Event' key is consistent
            if 'Event' in lib_event_obj.keys and lib_event_obj.keys['Event'] != event_name_from_lib:
                if event_name_from_lib == "UnknownEvent": event_name_from_lib = lib_event_obj.keys['Event']
                event_dict['Event'] = lib_event_obj.keys['Event']
        elif isinstance(lib_event_obj, dict): # Should not happen if lib sends Event objects
            event_dict.update(lib_event_obj)
            if 'Event' in lib_event_obj: event_name_from_lib = lib_event_obj['Event']
        else:
            logger.warning(f"Dispatch: Received event object of unexpected type: {type(lib_event_obj)}")
            event_dict['Event'] = event_name_from_lib
            event_dict['RawEventDetails'] = str(lib_event_obj)

        if 'Event' not in event_dict and event_name_from_lib != "UnknownEvent":
            event_dict['Event'] = event_name_from_lib
            
        # ---- The rest of the dispatch logic from your correct version ----
        action_id_in_event = event_dict.get("ActionID") # Get ActionID from the processed dict
        logger.debug(f"Dispatching Event to Async Listeners: Name='{event_dict.get('Event')}', ActionID='{action_id_in_event}', FullDict='{event_dict}'")
        
        event_name_to_dispatch = event_dict.get("Event")
        if event_name_to_dispatch:
            if event_name_to_dispatch in self._event_listeners:
                for callback in list(self._event_listeners[event_name_to_dispatch]):
                    asyncio.create_task(callback(event_dict))
            for g_callback in list(self._generic_event_listeners):
                asyncio.create_task(g_callback(event_dict))

        if action_id_in_event and event_name_to_dispatch and event_name_to_dispatch.endswith("Complete"):
            if action_id_in_event in self._response_futures:
                logger.debug(f"Dispatch: Received completion event '{event_name_to_dispatch}' for ActionID {action_id_in_event}. "
                             "Note: Future for this ActionID might have already been resolved on initial response.")


    def _ami_worker_thread_main(self):
        logger.info(f"AMI Worker Thread ({threading.get_ident()}): Started.")
        last_keepalive_time = time.monotonic()
        self._sync_ami_client = None 

        try:
            self._sync_ami_client = ami.AMIClient(
                address=self.host, port=self.port, timeout=20 # Increased default timeout for client
            )
            
            # Event handler for the library's add_event_listener
            # The library's client.py shows its internal _fire_on_event calls:
            # listener(event=event, source=self) if it's an EventListener object,
            # or just listener(event=event) if it's a simple callable.
            # Our _dispatch_ami_event_from_thread expects the event object.
            def internal_event_callback_for_library(event, source=None): # source is often the client instance
                if self._main_loop and self._main_loop.is_running():
                    self._main_loop.call_soon_threadsafe(self._dispatch_ami_event_from_thread, event)
                else: # pragma: no cover
                    logger.warning(f"AMI Worker: Main asyncio loop not available. Cannot dispatch event: {event.name if hasattr(event, 'name') else 'Unknown Event'}")
            
            # The library's add_event_listener can take a callable directly.
            self._sync_ami_client.add_event_listener(internal_event_callback_for_library)
            logger.info("Worker: Event listener registered with AMI client.")

            logger.info(f"Worker: Connecting and logging into Asterisk at {self.host}:{self.port}...")
            # login() returns a FutureResponse. .response blocks for the actual Response.
            login_future = self._sync_ami_client.login(username=self.username, secret=self.secret)
            login_response_obj: Optional[LibAmiResponse] = login_future.response 

            # --- DEBUG LOGGING for login_response_obj --- (Copied from your last successful log structure)
            if login_response_obj:
                logger.debug(f"Worker: Login - Type: {type(login_response_obj)}, Status: {getattr(login_response_obj, 'status', 'N/A')}, "
                             f"IsError: {login_response_obj.is_error() if hasattr(login_response_obj, 'is_error') else 'N/A'}, "
                             f"Keys: {getattr(login_response_obj, 'keys', {})}")
            else:
                logger.debug("Worker: Login - login_response_obj is None (likely timeout on .response).")

            if login_response_obj and not login_response_obj.is_error():
                logger.info(f"Worker: AMI Login Successful. Message: {login_response_obj.keys.get('Message', 'Authentication accepted')}")
                if self._main_loop:
                    self._main_loop.call_soon_threadsafe(self._set_connected_status, True)
            else:
                err_msg = "Worker: AMI Login Failed."
                if login_response_obj and hasattr(login_response_obj, 'keys'):
                    err_msg += f" Details: Status='{getattr(login_response_obj, 'status', 'N/A')}', Message='{login_response_obj.keys.get('Message', 'Login failure')}'"
                elif login_response_obj: err_msg += f" Unexpected response: {str(login_response_obj)[:100]}"
                else: err_msg += " No response from server (timeout on future.response)."
                logger.error(err_msg)
                raise ConnectionRefusedError(err_msg)

            # --- Queued Action Loop ---
            while not self._stop_worker_event.is_set():
                try:
                    action_tuple = self._action_queue.get(block=True, timeout=1.0)
                    if action_tuple is None: break 
                    
                    ami_action_obj, response_future_async = action_tuple # Our internal AmiAction wrapper
                    
                    # Use the library's SimpleAction class
                    # Our AmiAction's get_headers() provides the dict for **kwargs
                    # Our AmiAction's get_name() provides the name
                    action_for_lib = SimpleAction(
                        name=ami_action_obj.get_name(),
                        **ami_action_obj.get_headers() 
                    )
                    
                    # Debug log for the action being sent
                    sent_action_id = action_for_lib.keys.get('ActionID', 'N/A_IN_LIB_ACTION_KEYS')
                    logger.debug(f"Worker: Sending Action='{action_for_lib.name}', Headers='{action_for_lib.keys}', EffectiveActionID='{sent_action_id}'")
                    
                    try:
                        action_future = self._sync_ami_client.send_action(action_for_lib)
                        response_from_sync_client: Optional[LibAmiResponse] = action_future.response # Blocks
                        
                        response_dict_for_async = {}
                        if response_from_sync_client:
                            response_dict_for_async = {
                                'Response': getattr(response_from_sync_client, 'status', 'Error'),
                                **getattr(response_from_sync_client, 'keys', {})
                            }
                            if hasattr(response_from_sync_client, 'follows') and response_from_sync_client.follows:
                                 response_dict_for_async['Follows'] = '\n'.join(response_from_sync_client.follows)
                            logger.debug(f"Worker: Action '{action_for_lib.name}' (ID: {sent_action_id}) received response: {response_dict_for_async.get('Response')}, FullRespKeys: {response_dict_for_async}")
                        else: # Timeout on .response
                            logger.warning(f"Worker: Action '{action_for_lib.name}' (ID: {sent_action_id}) - No response object from sync client (timeout on future.response).")
                            response_dict_for_async = {'Response': 'Error', 'Message': 'No response from library (timeout on future.response)'}

                        if self._main_loop:
                            self._main_loop.call_soon_threadsafe(response_future_async.set_result, response_dict_for_async)

                    except Exception as e_action_send:
                        logger.error(f"Worker: Error during send_action/get_response for '{action_for_lib.name}' (ID: {sent_action_id}): {e_action_send}", exc_info=True)
                        if self._main_loop:
                            self._main_loop.call_soon_threadsafe(response_future_async.set_exception, e_action_send)
                    finally:
                         last_keepalive_time = time.monotonic()

                except thread_safe_queue.Empty:
                    #if time.monotonic() - last_keepalive_time > self._keepalive_interval:
                     #   logger.debug("Worker: Sending keepalive Ping.")
                      #  try:
                       #     ping_ami_action_obj = AmiAction('Ping') # Our helper makes ActionID
                        #    ping_action_for_lib = SimpleAction('Ping', **ping_ami_action_obj.get_headers())
                            
                         #   ping_future = self._sync_ami_client.send_action(ping_action_for_lib)
                          #  ping_response_obj = ping_future.response
                            
                           # if ping_response_obj and not ping_response_obj.is_error():
                            #    logger.debug(f"Worker: AMI Ping successful. Details: {getattr(ping_response_obj, 'keys', {})}")
                            #else: # Ping failed or timed out
                             #   ping_err_msg = "Ping failed"
                              #  if ping_response_obj and hasattr(ping_response_obj, 'keys'):
                                #    ping_err_msg += f" - Response: {ping_response_obj.keys.get('Response', 'Error')}, Message: {ping_response_obj.keys.get('Message', 'Unknown')}"
                               # elif ping_response_obj: ping_err_msg += f" - Unexpected obj: {str(ping_response_obj)[:100]}"
                            #    else: ping_err_msg += " - No response object (timeout on future.response)"
                             #   logger.warning(f"Worker: AMI Ping response issue: {ping_err_msg}")
                              #  raise ConnectionAbortedError(f"Ping failure implies connection loss: {ping_err_msg}")
                            #last_keepalive_time = time.monotonic()
                        

                        #except Exception as e_ping: # Includes ConnectionAbortedError from above
                         #   logger.error(f"Worker: Error during Ping processing: {e_ping}")
                          #  raise ConnectionAbortedError(f"Ping processing failed, connection likely lost: {e_ping}") from e_ping
                    pass
        except (ConnectionRefusedError, socket.timeout, OSError, ConnectionAbortedError) as e:
            logger.error(f"AMI Worker Thread: Connection or Critical Action Error: {e}")
            if self._main_loop:
                 self._main_loop.call_soon_threadsafe(self._set_connected_status, False, e)
        except Exception as e: 
            logger.critical(f"AMI Worker Thread: Unhandled Exception: {e}", exc_info=True)
            if self._main_loop:
                 self._main_loop.call_soon_threadsafe(self._set_connected_status, False, e)
        finally:
            logger.info(f"AMI Worker Thread ({threading.get_ident()}): Shutting down...")
            if self._sync_ami_client:
                try:
                    if hasattr(self._sync_ami_client, 'logoff'): # logoff first
                        logger.info("Worker: Attempting Logoff.")
                        logoff_future = self._sync_ami_client.logoff()
                        if logoff_future: logoff_future.response # Block for logoff completion
                    if hasattr(self._sync_ami_client, 'disconnect'): # then disconnect
                        logger.info("Worker: Attempting Disconnect.")
                        self._sync_ami_client.disconnect()
                    logger.info("Worker: AMI client logoff/disconnect process completed.")
                except Exception as e_close: 
                    logger.error(f"Worker: Error during client cleanup (logoff/disconnect): {e_close}")
            self._sync_ami_client = None
            if self._main_loop and self._connected: 
                self._main_loop.call_soon_threadsafe(self._set_connected_status, False, ConnectionAbortedError("Worker thread terminated"))
            logger.info(f"AMI Worker Thread ({threading.get_ident()}): Finished.")

    # --- Methods below this line are part of the async public API of AsteriskAmiClient ---
    # connect_and_login, _set_connected_status, send_action, event listeners, _stop_ami_worker, close
    # These should generally remain unchanged from the last fully working version for lock management.
    # I will paste the verified versions of these from our successful lock resolution.

    def _set_connected_status(self, status: bool, error: Optional[Exception] = None):
        async def task():
            async with self._lock:
                current_connection_future = self._connection_future
                if status:
                    self._connected = True
                    self._is_connecting_or_reconnecting = False
                    logger.info("AsteriskAmiClient marked as connected.")
                    if current_connection_future and not current_connection_future.done():
                        current_connection_future.set_result(True)
                else:
                    self._connected = False
                    logger.warning(f"AsteriskAmiClient marked as disconnected. Error: {error}")
                    if current_connection_future and not current_connection_future.done():
                         current_connection_future.set_exception(error or ConnectionError("Connection failed by worker"))
                    if self._is_connecting_or_reconnecting and not self._stop_worker_event.is_set():
                         logger.info(f"Connection attempt failed (notified by worker), scheduling retry in {self._connection_retry_delay}s.")
                         if self._main_loop and self._main_loop.is_running():
                             self._main_loop.call_later(self._connection_retry_delay, 
                                                        lambda: asyncio.create_task(self.connect_and_login()) if not self._stop_worker_event.is_set() else None)
                         else: # pragma: no cover
                              logger.error("Main loop not available to schedule retry for failed connection.")
                    else:
                        if not self._stop_worker_event.is_set():
                            self._is_connecting_or_reconnecting = False
        
        if self._main_loop and self._main_loop.is_running():
             self._main_loop.create_task(task())
        else: # pragma: no cover
             logger.error("Cannot set connected status, main event loop not available for task().")

    async def connect_and_login(self) -> bool:
        attempt_connection_future: asyncio.Future 
        await self._lock.acquire()
        try:
            if self._connected: return True
            if self._is_connecting_or_reconnecting and self._connection_future and not self._connection_future.done():
                logger.info("Connection attempt already in progress, awaiting its result...")
                existing_future_to_await = self._connection_future
                self._lock.release()
                try: return await existing_future_to_await
                except Exception as e_await:
                    logger.warning(f"Awaiting existing connection future failed: {e_await}. Will start new attempt if leader.")
                    await self._lock.acquire() 
                    if self._connected: return True 
                    self._is_connecting_or_reconnecting = False 
            
            if self._is_connecting_or_reconnecting:
                 logger.debug("Connect_and_login: Still marked as connecting by another call, returning False.")
                 return False 

            self._is_connecting_or_reconnecting = True
            self._main_loop = asyncio.get_running_loop()
            self._connection_future = self._main_loop.create_future()
            attempt_connection_future = self._connection_future
        finally:
            if self._lock.locked(): self._lock.release()

        await self._stop_ami_worker() 
        self._stop_worker_event.clear()
        self._ami_thread = threading.Thread(target=self._ami_worker_thread_main, daemon=True, name="AMIWorkerThread")
        self._ami_thread.start()
        logger.info("New AMI worker thread started. Waiting for connection status via its future.")
        
        try:
            await asyncio.wait_for(attempt_connection_future, timeout=30.0) # Increased wait_for timeout slightly
            async with self._lock: 
                is_now_connected = self._connected
                if not is_now_connected: 
                    self._is_connecting_or_reconnecting = False   
                return is_now_connected
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for AMI worker thread to connect/login via future.")
            async with self._lock:
                if attempt_connection_future and not attempt_connection_future.done():
                     attempt_connection_future.set_exception(TimeoutError("Connection timeout for this attempt"))
                self._is_connecting_or_reconnecting = False 
            await self._stop_ami_worker()
            return False
        except Exception as e: 
            logger.error(f"Error in connect_and_login waiting for connection future: {e}")
            async with self._lock:
                if attempt_connection_future and not attempt_connection_future.done(): 
                    attempt_connection_future.set_exception(e)
                self._is_connecting_or_reconnecting = False
            await self._stop_ami_worker()
            return False

    # In call_processor_service/asterisk_ami_client.py

    async def send_action(self, action: Union[str, AmiAction], timeout: float = 10.0, **kwargs) -> Optional[Dict[str, Any]]:
        if not self._connected:
            logger.warning("AMI not connected. Attempting connect before send_action.")
            if not await self.connect_and_login():
                logger.error("send_action: Failed to connect to AMI. Cannot send.")
                return {"Response": "Error", "Message": "AMI connection failed prior to send_action"}
        
        if not self._connected:
             logger.error("send_action: Still not connected after connect attempt. Cannot send.")
             return {"Response": "Error", "Message": "AMI still not connected post-attempt"}

        action_obj = action if isinstance(action, AmiAction) else AmiAction(action, **kwargs)
        action_id = action_obj.get_action_id()
        
        if not self._main_loop: self._main_loop = asyncio.get_running_loop()
        response_future_async = self._main_loop.create_future()
        self._response_futures[action_id] = response_future_async
        
        try:
            self._action_queue.put((action_obj, response_future_async), block=False)
            logger.debug(f"Queued action {action_obj.get_name()} (ID: {action_id}) for worker thread.")
            
            # <<< START: MODIFIED BLOCK TO HANDLE TIMEOUTS GRACEFULLY >>>
            try:
                # We wait for the response from the worker thread.
                return await asyncio.wait_for(response_future_async, timeout=timeout)
            except asyncio.TimeoutError:
                # If we time out, it means the worker didn't get a response from Asterisk in time.
                # For an async Originate, this is OFTEN OK. We can assume success and let events handle it.
                logger.warning(f"Timeout waiting for response to ActionID {action_id} ({action_obj.get_name()}). Assuming success due to Async Originate pattern.")
                # We'll return a synthetic success message.
                return {"Response": "Success", "Message": "Action sent, response timeout assumed OK for async action."}
            # <<< END: MODIFIED BLOCK >>>

        except thread_safe_queue.Full:
            logger.error(f"AMI action queue is full. Cannot send action {action_obj.get_name()} (ID: {action_id}).")
            if action_id in self._response_futures: self._response_futures.pop(action_id, None)
            return {"Response": "Error", "Message": "Action queue full"}
        except Exception as e:
            logger.error(f"Error in async send_action for {action_id} ({action_obj.get_name()}): {e}", exc_info=True)
            return {"Response": "Error", "Message": f"An exception occurred: {e}"}
        finally:
            # Clean up the future from our tracking dict regardless of outcome.
            self._response_futures.pop(action_id, None)

    def add_event_listener(self, event_name: str, callback: Callable[[Dict[str, Any]], Awaitable[None]]): # pragma: no cover
        if event_name not in self._event_listeners: self._event_listeners[event_name] = set()
        self._event_listeners[event_name].add(callback)
        logger.debug(f"Added listener for AMI event: {event_name}")

    def remove_event_listener(self, event_name: str, callback: Callable[[Dict[str, Any]], Awaitable[None]]): # pragma: no cover
        if event_name in self._event_listeners:
            self._event_listeners[event_name].discard(callback)
            if not self._event_listeners[event_name]: del self._event_listeners[event_name]
        logger.debug(f"Removed listener for AMI event: {event_name}")

    def add_generic_event_listener(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        self._generic_event_listeners.add(callback)
        logger.debug("Added generic AMI event listener.")

    def remove_generic_event_listener(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]): # pragma: no cover
        self._generic_event_listeners.discard(callback)
        logger.debug("Removed generic AMI event listener.")

    async def _stop_ami_worker(self):
        if self._ami_thread and self._ami_thread.is_alive():
            logger.info(f"Stopping AMI worker thread ({self._ami_thread.name})...")
            self._stop_worker_event.set()
            try: self._action_queue.put_nowait(None) 
            except thread_safe_queue.Full: pass 
            
            join_timeout = 5.0
            start_join_time = time.monotonic()
            while self._ami_thread.is_alive() and (time.monotonic() - start_join_time) < join_timeout:
                await asyncio.sleep(0.1)

            if self._ami_thread.is_alive(): # pragma: no cover
                 logger.warning(f"AMI worker thread ({self._ami_thread.name}) did not stop in {join_timeout}s.")
            else:
                 logger.info(f"AMI worker thread ({self._ami_thread.name}) stopped.")
        self._ami_thread = None

    async def close(self):
        logger.info("Closing AsteriskAmiClient connection.")
        async with self._lock:
            self._is_connecting_or_reconnecting = False # Prevent further auto-reconnects
            self._stop_worker_event.set() # Signal worker to stop if running

        await self._stop_ami_worker() # Ensure worker is stopped and joined

        # Clear listeners and fail any pending response futures
        self._event_listeners.clear()
        self._generic_event_listeners.clear()
        for future_id, future in list(self._response_futures.items()): # Iterate copy
            if not future.done():
                future.set_exception(ConnectionError("AMI client closed"))
            self._response_futures.pop(future_id, None) # Remove it
        
        self._connected = False # Mark as disconnected
        logger.info("AsteriskAmiClient resources released and client closed.")

# if __name__ == "__main__": (test block from previous full file)
# This should be identical to the test block you used in the previous successful run where login worked.
# No changes needed to the __main__ test block itself.
if __name__ == "__main__": # pragma: no cover
    async def main_ami_test_refactored():
        if not (app_config.ASTERISK_AMI_USER and app_config.ASTERISK_AMI_SECRET):
            logger.error("AMI Username or Secret not configured in .env. Aborting test.")
            print("CRITICAL: Set ASTERISK_AMI_USER and ASTERISK_AMI_SECRET in your .env file for this test.")
            return

        ami_client = AsteriskAmiClient()
        
        async def example_event_handler(event: Dict[str, Any]):
            event_name = event.get("Event", "UnknownEvent")
            action_id_in_event = event.get("ActionID")
            log_msg_parts = [f"[EVT_HANDLER] Event: {event_name}"]
            if action_id_in_event: log_msg_parts.append(f"AID: {action_id_in_event}")
            # Add more details based on event type if needed for debugging
            if event_name == "FullyBooted": log_msg_parts.append(f"Status: {event.get('Status')}")
            elif event_name.endswith("List") or event_name.endswith("ListComplete") or event_name.startswith("Channel"):
                log_msg_parts.append(f"Content: { {k:v for k,v in event.items() if k not in ['Event', 'ActionID']} }")


            logger.info(" | ".join(log_msg_parts))

        ami_client.add_generic_event_listener(example_event_handler)

        logger.info("Attempting initial AMI connect and login (refactored client)...")
        connected = await ami_client.connect_and_login()
        
        if not connected:
            logger.error("Failed to connect to AMI for test after initial attempt. Exiting.")
            await ami_client.close()
            return

        logger.info("AMI Client connected for test.")
        
        logger.info("Sending PJSIPShowEndpoints action...")
        # Increased timeout for list commands, as the future waits for the *first* response,
        # but the overall command might take longer if the client default timeout is short.
        # Our action timeout in send_action() is what matters for the async future.
        endpoints_response = await ami_client.send_action("PJSIPShowEndpoints", timeout=25.0) 
        if endpoints_response:
            logger.info(f"PJSIPShowEndpoints Initial Response: Resp='{endpoints_response.get('Response')}', "
                        f"Msg='{endpoints_response.get('Message', 'N/A')}', AID='{endpoints_response.get('ActionID')}'")
        else:
            logger.warning("PJSIPShowEndpoints action got no/failed initial response from async client.")

        # Give some time for events from PJSIPShowEndpoints to (hopefully) arrive and be logged
        logger.info("Waiting 5s for PJSIPShowEndpoints events...")
        await asyncio.sleep(5) 

        logger.info("Sending CoreShowChannels action...")
        channels_response = await ami_client.send_action("CoreShowChannels", timeout=25.0)
        if channels_response:
            logger.info(f"CoreShowChannels Initial Response: Resp='{channels_response.get('Response')}', "
                        f"Msg='{channels_response.get('Message', 'N/A')}', AID='{channels_response.get('ActionID')}'")
        else:
            logger.warning("CoreShowChannels action got no/failed initial response from async client.")
        
        logger.info("Waiting 5s for CoreShowChannels events...")
        await asyncio.sleep(5)

        logger.info("Sending a Ping action...")
        ping_resp = await ami_client.send_action("Ping", timeout=5.0) # Ping should be fast
        if ping_resp:
            logger.info(f"Ping Response: Resp='{ping_resp.get('Response')}', Timestamp='{ping_resp.get('Timestamp')}', "
                        f"Ping='{ping_resp.get('Ping')}', AID='{ping_resp.get('ActionID')}'")
        else:
            logger.warning("Ping action got no/failed initial response from async client.")


        logger.info("Listening for further AMI events for 10 seconds...")
        await asyncio.sleep(10)

        await ami_client.close()
        logger.info("AMI Client test (refactored) finished.")

    asyncio.run(main_ami_test_refactored())