#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Deepgram TTS Module (Asynchronous Version)

This module handles text-to-speech conversion using Deepgram's TTS API.
Uses an asynchronous WebSocket connection for compatibility with older websockets versions.
"""

import logging
import json
import threading
import queue
import asyncio
import websockets
import base64

# Get the logger
logger = logging.getLogger(__name__)

class DeepgramTTS:
    """Handler for Deepgram TTS streaming with asynchronous WebSocket connection."""
    
    def __init__(self, api_key, model="aura-2-thalia-en", sample_rate=8000, encoding="mulaw"):
        """
        Initialize the Deepgram TTS handler.
        
        Args:
            api_key (str): Deepgram API key
            model (str): Voice model to use
            sample_rate (int): Sample rate for audio output
            encoding (str): Audio encoding format
        """
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        self.encoding = encoding
        self.websocket = None
        self.connected = False
        
        # Use a thread-safe queue for audio data
        self.audio_queue = queue.Queue()
        
        # Threading and asyncio control
        self.exit_event = threading.Event()
        self.receiver_task = None
        self.loop = None
        
        # Callbacks
        self.on_audio_callback = None
        self.on_error_callback = None
        self.on_close_callback = None
    
    async def _connect(self):
        """Establish connection to Deepgram TTS API asynchronously."""
        try:
            # Construct the URL with all parameters
            url = f"wss://api.deepgram.com/v1/speak?encoding={self.encoding}&sample_rate={self.sample_rate}&container=none&model={self.model}"
            
            # Connect using the asynchronous WebSocket client
            self.websocket = await websockets.connect(
                url, extra_headers={"Authorization": f"Token {self.api_key}"}
            )
            
            self.connected = True
            logger.info(f"Connected to Deepgram TTS API with model {self.model}")
            
            # Start the receiver task
            self.exit_event.clear()
            self.receiver_task = asyncio.create_task(self._receiver_loop())
            
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Deepgram TTS API: {str(e)}")
            if self.on_error_callback:
                self.on_error_callback(f"Connection error: {str(e)}")
            return False
    
    def connect(self):
        """
        Establish connection to Deepgram TTS API.
        This is a synchronous wrapper around the async _connect method.
        """
        try:
            logger.info("DEEPGRAM DEBUG: Starting connection to Deepgram TTS API")
            logger.info(f"DEEPGRAM DEBUG: API Key (first 5 chars): {self.api_key[:5]}...")
            logger.info(f"DEEPGRAM DEBUG: Model: {self.model}")
            logger.info(f"DEEPGRAM DEBUG: Sample rate: {self.sample_rate}")
            logger.info(f"DEEPGRAM DEBUG: Encoding: {self.encoding}")
            
            # Check if we're already in an event loop
            try:
                running_loop = asyncio.get_running_loop()
                logger.info("DEEPGRAM DEBUG: Already in an event loop, using it for connection")
                # Create a future to track the result
                future = running_loop.create_future()
                
                # Define a callback to set the future result
                async def run_connect():
                    try:
                        logger.info("DEEPGRAM DEBUG: Running _connect in existing event loop")
                        result = await self._connect()
                        logger.info(f"DEEPGRAM DEBUG: _connect result: {result}")
                        future.set_result(result)
                    except Exception as e:
                        logger.error(f"DEEPGRAM DEBUG: Error in connect task: {str(e)}")
                        import traceback
                        logger.error(f"DEEPGRAM DEBUG: Traceback: {traceback.format_exc()}")
                        future.set_exception(e)
                
                # Create the task
                running_loop.create_task(run_connect())
                logger.info("DEEPGRAM DEBUG: Created task in existing event loop")
                # We can't wait for the future here, so we return True and let the task complete
                return True
            except RuntimeError:
                # No running event loop, create a new one
                logger.info("DEEPGRAM DEBUG: No running event loop, creating a new one")
                if self.loop is None or self.loop.is_closed():
                    self.loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.loop)
                    logger.info("DEEPGRAM DEBUG: Created new event loop")
                
                result = self.loop.run_until_complete(self._connect())
                logger.info(f"DEEPGRAM DEBUG: _connect result from new loop: {result}")
                return result
        except Exception as e:
            logger.error(f"DEEPGRAM DEBUG: Error in connect: {str(e)}")
            import traceback
            logger.error(f"DEEPGRAM DEBUG: Traceback: {traceback.format_exc()}")
            return False
    
    async def _disconnect(self):
        """Close connection to Deepgram TTS API asynchronously."""
        if self.websocket:
            try:
                # Send close command
                await self.websocket.send(json.dumps({"type": "Close"}))
                await self.websocket.close()
                self.websocket = None
                self.connected = False
                logger.info("Disconnected from Deepgram TTS API")
            except Exception as e:
                logger.error(f"Error disconnecting from Deepgram TTS API: {str(e)}")
        
        if self.receiver_task:
            self.receiver_task.cancel()
            try:
                await self.receiver_task
            except asyncio.CancelledError:
                pass
            self.receiver_task = None
    
    def disconnect(self):
        """
        Close connection to Deepgram TTS API.
        This is a synchronous wrapper around the async _disconnect method.
        """
        self.exit_event.set()
        
        try:
            # Check if we're already in an event loop
            try:
                running_loop = asyncio.get_running_loop()
                # If we're in an event loop, create a task instead of run_until_complete
                logger.info("Already in event loop, creating disconnect task")
                asyncio.create_task(self._disconnect())
                return
            except RuntimeError:
                # No running event loop, use our own
                if self.loop and not self.loop.is_closed():
                    # No running event loop, safe to use run_until_complete
                    self.loop.run_until_complete(self._disconnect())
        except Exception as e:
            logger.error(f"Error in disconnect: {str(e)}")
    
    async def _stream_text(self, text):
        """Stream text to Deepgram TTS API asynchronously."""
        if not self.connected or not self.websocket:
            logger.warning("Not connected to Deepgram TTS API")
            return False
        
        try:
            # Send the text as a Speak command
            await self.websocket.send(json.dumps({
                "type": "Speak",
                "text": text
            }))
            logger.info(f"Sent text to Deepgram TTS: {text[:50]}...")
            
            # Send a Flush command to receive the audio
            await self.websocket.send(json.dumps({
                "type": "Flush"
            }))
            logger.info("Sent flush command to Deepgram TTS")
            
            return True
        except Exception as e:
            logger.error(f"Error sending text to Deepgram TTS: {str(e)}")
            if self.on_error_callback:
                self.on_error_callback(f"Send error: {str(e)}")
            return False
    
    def stream_text(self, text):
        """
        Stream text to Deepgram TTS API.
        This is a synchronous wrapper around the async _stream_text method.
        
        Args:
            text (str): Text to convert to speech
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        try:
            logger.info(f"DEEPGRAM DEBUG: stream_text called with text: '{text}'")
            logger.info(f"DEEPGRAM DEBUG: Connected status: {self.connected}")
            
            # Check if we're already in an event loop
            try:
                running_loop = asyncio.get_running_loop()
                # If we're in an event loop, create a task instead of run_until_complete
                logger.info("DEEPGRAM DEBUG: Already in event loop, creating stream_text task")
                # Create a future to track the result
                future = running_loop.create_future()
                
                # Define a callback to set the future result
                async def run_stream_text():
                    try:
                        logger.info("DEEPGRAM DEBUG: Running _stream_text in existing event loop")
                        result = await self._stream_text(text)
                        logger.info(f"DEEPGRAM DEBUG: _stream_text result: {result}")
                        future.set_result(result)
                    except Exception as e:
                        logger.error(f"DEEPGRAM DEBUG: Error in stream_text task: {str(e)}")
                        import traceback
                        logger.error(f"DEEPGRAM DEBUG: Traceback: {traceback.format_exc()}")
                        future.set_exception(e)
                
                # Create the task
                task = running_loop.create_task(run_stream_text())
                logger.info(f"DEEPGRAM DEBUG: Created task: {task}")
                return True  # Return immediately, don't wait for the future
            except RuntimeError:
                # No running event loop, use our own
                logger.info("DEEPGRAM DEBUG: No running event loop, using our own")
                if self.loop and not self.loop.is_closed():
                    # No running event loop, safe to use run_until_complete
                    logger.info("DEEPGRAM DEBUG: Using existing loop for run_until_complete")
                    result = self.loop.run_until_complete(self._stream_text(text))
                    logger.info(f"DEEPGRAM DEBUG: _stream_text result from run_until_complete: {result}")
                    return result
                else:
                    logger.error("DEEPGRAM DEBUG: No event loop available for stream_text")
                    return False
        except Exception as e:
            logger.error(f"DEEPGRAM DEBUG: Error in stream_text: {str(e)}")
            import traceback
            logger.error(f"DEEPGRAM DEBUG: Traceback: {traceback.format_exc()}")
            return False
    
    async def _receiver_loop(self):
        """Receive audio data from Deepgram TTS API asynchronously."""
        try:
            while not self.exit_event.is_set() and self.websocket:
                try:
                    # Receive message from Deepgram
                    message = await self.websocket.recv()
                    
                    if isinstance(message, str):
                        # Handle text messages (usually status updates)
                        try:
                            data = json.loads(message)
                            logger.debug(f"Received message from Deepgram: {data}")
                        except:
                            logger.debug(f"Received non-JSON message: {message}")
                    elif isinstance(message, bytes):
                        # Handle binary audio data
                        logger.info(f"Received {len(message)} bytes of audio data from Deepgram TTS")
                        
                        # Add to queue
                        self.audio_queue.put(message)
                        
                        # Call callback if set
                        if self.on_audio_callback:
                            self.on_audio_callback(message)
                except websockets.exceptions.ConnectionClosed:
                    logger.info("Deepgram TTS connection closed")
                    break
                except Exception as e:
                    logger.error(f"Error receiving from Deepgram TTS: {str(e)}")
                    if self.on_error_callback:
                        self.on_error_callback(f"Receive error: {str(e)}")
        except asyncio.CancelledError:
            logger.info("Receiver task cancelled")
        finally:
            self.connected = False
            if self.on_close_callback:
                self.on_close_callback()
    
    def get_audio(self, timeout=0.1):
        """
        Get audio data from the queue.
        
        Args:
            timeout (float): Timeout in seconds
            
        Returns:
            bytes: Audio data or None if no data is available
        """
        try:
            audio_data = self.audio_queue.get(block=True, timeout=timeout)
            if audio_data:
                logger.info(f"Retrieved {len(audio_data)} bytes of audio data from queue")
            return audio_data
        except queue.Empty:
            return None
        except Exception as e:
            logger.error(f"Error getting audio from queue: {str(e)}")
            return None
    
    def set_audio_callback(self, callback):
        """
        Set callback for received audio data.
        
        Args:
            callback (callable): Function to call with audio data
        """
        self.on_audio_callback = callback
    
    def set_error_callback(self, callback):
        """
        Set callback for errors.
        
        Args:
            callback (callable): Function to call with error message
        """
        self.on_error_callback = callback
    
    def set_close_callback(self, callback):
        """
        Set callback for connection close.
        
        Args:
            callback (callable): Function to call when connection closes
        """
        self.on_close_callback = callback