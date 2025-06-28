# ===============================================================================
# LEGACY FILE - NOT CURRENTLY USED IN ACTIVE SYSTEM (v16.0+)
# 
# Status: PRESERVED for reference/potential future use
# Last Active: Experimental development phases (v13-v15)
# Replacement: N/A - Current system uses Asterisk AudioSocket instead of Twilio
# Safe to ignore: This file is not imported by main.py or active services
# 
# Historical Context: Experimental Twilio Media Streams + OpenAI Realtime + Deepgram TTS
#                    implementation. Current system uses Asterisk AudioSocket with
#                    full OpenAI Realtime API for better control and integration.
# ===============================================================================
#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Twilio Handler Module (OpenAI Realtime Experimental Version)

This module handles Twilio integration for making outbound calls using Media Streams with FastAPI and WebSockets.
It connects Twilio's Media Streams to OpenAI's Realtime API for real-time voice conversation.

EXPERIMENTAL VERSION:
- Changes modality for OpenAI realtime to text and audio
- Collects and prints the complete text when response.text.done is received
- Based on the twilio_handler_realtime.py implementation
- Acts as a direct bridge between Twilio and OpenAI without needing conversation.py

IMPORTANT NOTES:
- Voice Activity Detection (VAD) is FULLY handled by OpenAI's Realtime API
- This implementation COMPLETELY REMOVES local VAD processing
- The OpenAI Realtime API automatically detects when the user is speaking and handles turn-taking
- No audio is passed to the local VAD module, all speech detection is done by OpenAI
- This handler directly manages the conversation flow without needing conversation.py
- All LLM decisions, speech-to-text, and text-to-speech are handled by OpenAI
"""

import os
import json
import base64
import asyncio
import websockets
import threading
import time
import socket
import logging
import sys
sys.stdout.reconfigure(encoding='utf-8')
import queue
import pickle
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from twilio.base.exceptions import TwilioRestException
import uvicorn
import requests

# Get the logger
logger = logging.getLogger(f"OpenDeep.{__name__}") # <--- THE CRITICAL CHANGE


class TwilioHandler:
    """Handler for Twilio integration with Media Streams using FastAPI and OpenAI Realtime."""
    
    def __init__(self, config, twilio_to_api_queue=None, api_to_twilio_queue=None,
                 api_ready_event=None, stop_event=None,logger=None):
        """
        Initialize the Twilio handler.
        
        Args:
            config (Config): The application configuration
            twilio_to_api_queue (queue.Queue): Queue for audio from Twilio to API
            api_to_twilio_queue (queue.Queue): Queue for audio from API to Twilio
            api_ready_event (threading.Event): Event to signal API readiness
            stop_event (threading.Event): Event to signal stop
        """
        print(" Initialize Twilio Handler")
        logger = logging.getLogger(__name__)
        logger.info("INIT Twilio handler")
        self.config = config
        self.client = self._initialize_client()
        self.call = None
        
        # Communication primitives
        self.twilio_to_api_queue = twilio_to_api_queue or queue.Queue()
        self.api_to_twilio_queue = api_to_twilio_queue or queue.Queue()
        self.api_ready_event = api_ready_event or threading.Event()
        self.stop_event = stop_event or threading.Event()
        
        # Media Streams settings
        self.app = FastAPI()
        self.server = None
        self.server_thread = None
        self.server_running = False
        self.stream_running = False  # For compatibility with conversation.py
        self.port = 8080  # Default port
        self.stream_url = None
        
        # Locks for thread safety
        self.playback_lock = asyncio.Lock()  # Lock for playback-related state
        self.deepgram_lock = asyncio.Lock()  # Lock for Deepgram WebSocket operations
        self.openai_lock = asyncio.Lock()    # Lock for OpenAI WebSocket operations
        self.text_response_lock = asyncio.Lock()  # Lock for current_text_response modifications
        
        # Connection specific state
        self.current_twilio_websocket = None  # Store the current websocket connection
        self.current_stream_sid = None
        self.latest_media_timestamp = 0
        self.last_assistant_item = None
        self.mark_queue = []
        self.response_start_timestamp_twilio = None
        self.openai_session_updated = False # Flag for OpenAI session status
        self.initial_hello_sent = False # Flag to ensure initial message sent only once
        self.stream_started = False # Flag to indicate that the stream has started
        self._audio_chunk_counter = 0 # Counter for audio chunks sent to Twilio
        
        # Callbacks
        # No audio callback - VAD is handled by OpenAI
        self.on_mark_callback = None
        self.on_close_callback = None
        
        # For collecting complete text response (EXPERIMENTAL)
        self.current_text_response = ""
        
        # System prompt for OpenAI (used when no conversation manager is present)
        self.system_prompt = None
        
        # API connections
        self.openai_ws = None  # Initialize OpenAI WebSocket connection
        
        # Deepgram TTS integration
        self.deepgram_ws = None
        self.use_deepgram_tts = True  # Always connect to Deepgram TTS
        self.is_playing_response = False  # Flag to track when we're playing a response
        self.current_sequence_id = None   # Track the current Deepgram sequence_id
        self.is_first_sequence = True     # Flag to track if this is the first sequence (not interruptible)
        self.use_local_playback = False   # Disable local audio playback
        self.speaker = None               # No speaker for local audio playback
        self.speaker_lock = threading.Lock()  # Lock for speaker operations
        
        # Pre-generated audio
        self.greeting_audio_chunks = []  # Store pre-generated greeting audio chunks
        self.ack_audio_chunks_1 = []     # Store pre-generated "hmm Ok!" audio chunks
        self.ack_audio_chunks_2 = []     # Store pre-generated "Sure!" audio chunks
        self.greeting_audio_file = os.path.join("data", "audio", "pregenerated_audio.bin")
        self.greeting_text = "This is an AI call from Star Insurance"
        self.ack_text_1 = "mmhhmm!"
        self.ack_text_2 = "oh!"
        self.greeting_generated = False  # Flag to track if greeting has been generated
        self.voice_model = "aura-2-amalthea-en"  # Current voice model
        
        # Set up routes
        self._setup_routes()
        
    def run_api_loop(self):
        """Run the API loop in a separate thread."""
        try:
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Run the API main coroutine
            loop.run_until_complete(self._api_main())
            
            # Close the loop when done
            loop.close()
        except Exception as e:
            logger.error(f"Error in API loop: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Signal stop on critical errors
            self.stop_event.set()

    async def _api_main(self):
        """Main coroutine for the API thread."""
        # Initialize task variables to None
        openai_read_task = None
        deepgram_read_task = None
        queue_to_openai_task = None
        
        try:
            # Connect to OpenAI
            logger.info("Connecting to OpenAI Realtime API...")
            openai_url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
            openai_headers = {
                "Authorization": f"Bearer {self.config.openai_api_key}",
                "OpenAI-Beta": "realtime=v1",
                "modalities": ["text"]
            }
            
            self.openai_ws = await websockets.connect(
                openai_url,
                extra_headers=openai_headers,
                ping_interval=30  # Keep connection alive
            )
            logger.info("Connected to OpenAI Realtime API")
            
            # Send session update
            await self._send_session_update(self.openai_ws)
            logger.info("Session update sent to OpenAI")
            
            # Connect to Deepgram if enabled
            if self.use_deepgram_tts:
                logger.info("Connecting to Deepgram TTS API...")
                self.deepgram_ws = await self._connect_to_deepgram(reconnect=False)
                if not self.deepgram_ws:
                    logger.warning("Failed to connect to Deepgram TTS")
                else:
                    logger.info("Connected to Deepgram TTS API")
                    
                    # Check if the greeting file exists and has the correct voice model
                    if os.path.exists(self.greeting_audio_file):
                        try:
                            with open(self.greeting_audio_file, 'rb') as f:
                                saved_data = pickle.load(f)
                                
                            # Check if the saved data is in the new format and has a different voice model
                            if (isinstance(saved_data, dict) and
                                'voice_model' in saved_data and
                                saved_data['voice_model'] != self.voice_model):
                                
                                # Delete the file to force regeneration with the new voice model
                                os.remove(self.greeting_audio_file)
                                logger.info(f"Deleted greeting audio file with old voice model: {saved_data['voice_model']}")
                        except Exception as e:
                            logger.error(f"Error checking greeting audio file: {str(e)}")
                    
                    # Generate the greeting audio
                    logger.info("Generating greeting audio...")
                    greeting_success = await self._generate_greeting()
                    if greeting_success:
                        logger.info("Greeting audio generated successfully")
                    else:
                        logger.warning("Failed to generate greeting audio")
            
            # Signal that APIs are ready
            essential_connections = self.openai_ws is not None
            if essential_connections:
                logger.info("Essential API connections established, signaling readiness")
                self.api_ready_event.set()
            else:
                logger.error("Essential API connections failed")
                self.stop_event.set()
                return
            
            # Create tasks for handling API communication
            openai_read_task = asyncio.create_task(self._api_handle_openai_read())
            deepgram_read_task = None
            if self.deepgram_ws:
                deepgram_read_task = asyncio.create_task(self._api_handle_deepgram_read())
                logger.info("Deep Gram is ALive")
            queue_to_openai_task = asyncio.create_task(self._api_handle_queue_to_openai())
            
            # Keep running until stop event is set
            while not self.stop_event.is_set():
                # Check if we can send the initial hello (only if it hasn't been sent yet)
                if not self.initial_hello_sent:
                    await self._maybe_send_initial_hello(self.openai_ws)
                    logger.info("Initial hello sent")
                
                await asyncio.sleep(.3)  # Small sleep to prevent CPU hogging
            
            logger.info("API thread received stop event")
        except Exception as e:
            logger.error(f"Error in API main coroutine: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.stop_event.set()
        finally:
            # Cancel tasks
            tasks = [t for t in [openai_read_task, deepgram_read_task, queue_to_openai_task]
                    if t is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            
            # Close API connections
            if self.openai_ws:
                await self.openai_ws.close()
                self.openai_ws = None
                logger.info("OpenAI connection closed")
            
            if self.deepgram_ws:
                try:
                    await self.deepgram_ws.send(json.dumps({"type": "Close"}))
                    await self.deepgram_ws.close()
                except:
                    pass
                self.deepgram_ws = None
                logger.info("Deepgram connection closed")
    
    async def _api_handle_openai_read(self):
        """Handle messages from OpenAI."""
        try:
            async for openai_message in self.openai_ws:
                if self.stop_event.is_set():
                    break
                    
                response = json.loads(openai_message)
                event_type = response.get('type', '')
                
                # Log important events
                logger.debug(f"Received OpenAI event: {event_type}")
                
                # Handle session updated event
                if event_type == 'session.updated':
                    logger.info("OpenAI session updated")
                    async with self.playback_lock:
                        self.openai_session_updated = True
                
                # Handle response.text.delta event
                elif event_type == 'response.text.delta' and 'delta' in response:
                    # Extract the delta field from the JSON response
                    delta_text = response['delta']
                    # Print the delta field value with color
                    print(f"\033[1;35m{delta_text}\033[0m", end="", flush=True)  # Magenta text
                    
                    # Log the delta value
                    logger.info(f"DELTA TEXT: {delta_text}")
                    
                    # Accumulate text with lock protection
                    async with self.text_response_lock:
                        self.current_text_response += delta_text
                        accumulated_text = self.current_text_response
                        # Count words by splitting on whitespace
                        word_count = len(accumulated_text.split())
                    
                    # Only send accumulated text to Deepgram if:
                    # 1. We have 50+ words AND receive punctuation
                    if self.deepgram_ws and any(char in delta_text for char in [',', '.', '?', '!', ';', ':']) and word_count >= 50:
                        # Send the accumulated text
                        await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": accumulated_text}))
                        logger.info(f"Sent accumulated text to Deepgram TTS: '{accumulated_text}' (Word count: {word_count})")
                        
                        # Set playing flag
                        async with self.playback_lock:
                            self.is_playing_response = True
                        
                        # Flush to generate audio
                        await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
                        logger.info("Sent flush to Deepgram TTS")
                        
                        # Reset accumulated text after sending
                        async with self.text_response_lock:
                            self.current_text_response = ""
                
                # Handle text.done event - send any remaining text and flush to Deepgram TTS
                elif event_type == 'response.text.done':
                    logger.info("OPENAI TEXT DONE EVENT")
                    
                    async with self.text_response_lock:
                        final_text = response.get('text', self.current_text_response)
                        accumulated_text = self.current_text_response
                        word_count = len(accumulated_text.split())
                        self.current_text_response = ""
                    
                    logger.info(f"COMPLETE TEXT: {final_text} (Word count: {word_count})")
                    
                    # Send any accumulated text and flush to Deepgram if available
                    # For text.done, we always send the text regardless of word count
                    if self.deepgram_ws:
                        # If there's accumulated text that hasn't been sent yet, send it
                        if accumulated_text.strip():
                            await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": final_text}))
                            logger.info(f"Sent final text to Deepgram TTS: '{final_text}' (Word count: {word_count})")
                        
                        # Set playing flag
                        async with self.playback_lock:
                            self.is_playing_response = True
                        
                        # Flush to generate audio
                        await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
                        logger.info("Sent final flush to Deepgram TTS")
                
                # Handle transcription from OpenAI
                elif event_type == 'input_audio_buffer.transcription' and 'text' in response:
                    transcription = response['text']
                    logger.info(f"Transcription: '{transcription}'")
                
                # Handle VAD events
                elif event_type == 'input_audio_buffer.speech_started':
                    logger.info("Speech started - stopping TTS if playing")
                    await self._api_stop_deepgram_tts()
                    
                    # Signal Twilio to clear audio
                    self.api_to_twilio_queue.put({'type': 'clear'})
                    
                    # Randomly choose between the two acknowledgment responses
                    import random
                    ack_chunks = self.ack_audio_chunks_1 if random.random() < 0.5 else self.ack_audio_chunks_2
                    ack_text = self.ack_text_1 if ack_chunks == self.ack_audio_chunks_1 else self.ack_text_2
                    
                    # Send the acknowledgment audio to Twilio
                    if ack_chunks:
                        logger.info(f"Sending acknowledgment response: '{ack_text}'")
                        
                        # Small delay to ensure clear is processed first
                        await asyncio.sleep(0.1)
                        
                        # Send each audio chunk to Twilio
                        for chunk in ack_chunks:
                            self.api_to_twilio_queue.put({'type': 'audio', 'payload': chunk})
                            
                            # Small delay to avoid overwhelming the queue
                            await asyncio.sleep(0.01)
                        
                        logger.info(f"Queued acknowledgment audio for Twilio: '{ack_text}'")
        except Exception as e:
            logger.error(f"Error handling OpenAI messages: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.stop_event.set()

    async def _api_handle_deepgram_read(self):
        """Handle audio from Deepgram TTS."""
        try:
            if not self.deepgram_ws:
                return
                
            async for message in self.deepgram_ws:
                if self.stop_event.is_set():
                    break
                    
                if isinstance(message, bytes):
                    # Put audio bytes on the queue to Twilio
                    self.api_to_twilio_queue.put({'type': 'audio', 'payload': message})
                    logger.debug(f"Sent {len(message)} audio bytes to Twilio queue")
                elif isinstance(message, str):
                    # Process JSON messages (Cleared, Flushed, etc.)
                    try:
                        data = json.loads(message)
                        logger.debug(f"Deepgram message: {data}")
                    except:
                        pass
        except Exception as e:
            logger.error(f"Error handling Deepgram messages: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    async def _api_handle_queue_to_openai(self):
        """Handle audio from Twilio to OpenAI queue."""
        try:
            loop = asyncio.get_running_loop()
            
            while not self.stop_event.is_set():
                try:
                    # Use run_in_executor to get from queue without blocking the event loop
                    audio_data = await loop.run_in_executor(
                        None,
                        lambda: self.twilio_to_api_queue.get(block=True, timeout=0.1)
                    )
                    
                    # Send to OpenAI
                    if self.openai_ws and not self.openai_ws.closed:
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": audio_data
                        }
                        await self.openai_ws.send(json.dumps(audio_append))
                        # logger.debug("Sent audio to OpenAI")
                except queue.Empty:
                    # This is normal, just means no audio is available yet
                    await asyncio.sleep(0.01)
                except Exception as e:
                    logger.error(f"Error sending audio to OpenAI: {str(e)}")
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in queue to OpenAI handler: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.stop_event.set()
            
    async def _api_send_text_to_deepgram(self, text):
        """Send text to Deepgram TTS."""
        try:
            if not self.deepgram_ws or self.deepgram_ws.closed:
                logger.warning("Cannot send to Deepgram: WebSocket is closed or None")
                return False
            
            # Send the text
            await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": text}))
            logger.info(f"Sent text to Deepgram TTS: '{text}'")
            
            # Set playing flag
            async with self.playback_lock:
                self.is_playing_response = True
            
            # Flush to generate audio
            await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
            logger.info("Sent flush to Deepgram TTS")
            
            return True
        except Exception as e:
            logger.error(f"Error sending text to Deepgram: {str(e)}")
            return False

    async def _api_stop_deepgram_tts(self):
        """Stop Deepgram TTS playback."""
        try:
            if not self.deepgram_ws or self.deepgram_ws.closed:
                return False
            
            # Send Clear message
            await self.deepgram_ws.send(json.dumps({"type": "Clear"}))
            logger.info("Sent Clear to Deepgram TTS")
            
            # Update state
            async with self.playback_lock:
                self.is_playing_response = False
                self.is_first_sequence = True
            
            return True
        except Exception as e:
            logger.error(f"Error stopping Deepgram TTS: {str(e)}")
            return False
            
    def _initialize_client(self):
        """Initialize the Twilio client."""
        try:
            # Get Twilio credentials from config
            account_sid = self.config.twilio_account_sid
            auth_token = self.config.twilio_auth_token
            
            # Create the client
            client = Client(account_sid, auth_token)
            
            return client
        except Exception as e:
            logger.error(f"Error initializing Twilio client: {str(e)}")
            raise
            
    async def _stop_deepgram_playback(self):
        """Stop the current Deepgram TTS playback using the Clear message and stop local audio playback."""
        success = False
        
        # Use the deepgram lock for WebSocket operations
        async with self.deepgram_lock:
            # Stop Deepgram playback
            if self.deepgram_ws:
                try:
                    # Send Clear message to stop current playback and clear the buffer
                    await self.deepgram_ws.send(json.dumps({"type": "Clear"}))
                    logger.info("Sent Clear message to Deepgram TTS to stop playback and clear buffer")
                    success = True
                except Exception as e:
                    logger.error(f"Error stopping Deepgram TTS playback: {str(e)}")
        
        # Local audio playback is disabled in this version
        logger.debug("Local audio playback is disabled in this version")
        
        # Use the playback lock for state changes
        async with self.playback_lock:
            # Reset the playing flag and first sequence flag
            self.is_playing_response = False
            self.is_first_sequence = True  # Reset to make the next sequence non-interruptible again
        
        return success
    
    async def _generate_greeting(self):
        """
        Generate the greeting audio using Deepgram TTS.
        
        This method checks if the greeting audio file already exists.
        If it does, it loads the audio chunks from the file.
        If it doesn't, it generates the audio using Deepgram TTS and saves it to a file.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Check if the greeting audio file already exists
            if os.path.exists(self.greeting_audio_file):
                logger.info(f"Loading pre-generated greeting from {self.greeting_audio_file}")
                try:
                    with open(self.greeting_audio_file, 'rb') as f:
                        # Load the audio chunks and voice model from the file
                        saved_data = pickle.load(f)
                        
                        # Check if the saved data is in the new format (dict with voice_model)
                        if isinstance(saved_data, dict) and 'voice_model' in saved_data:
                            saved_voice_model = saved_data.get('voice_model')
                            
                            # Check if the voice model has changed
                            if saved_voice_model != self.voice_model:
                                logger.info(f"Voice model changed from {saved_voice_model} to {self.voice_model}, will regenerate audio")
                                return False
                            
                            # Check if the saved data has all the required audio chunks
                            if all(key in saved_data for key in ['greeting_chunks', 'ack_chunks_1', 'ack_chunks_2']):
                                self.greeting_audio_chunks = saved_data.get('greeting_chunks', [])
                                self.ack_audio_chunks_1 = saved_data.get('ack_chunks_1', [])
                                self.ack_audio_chunks_2 = saved_data.get('ack_chunks_2', [])
                                
                                logger.info(f"Loaded {len(self.greeting_audio_chunks)} greeting audio chunks, " +
                                           f"{len(self.ack_audio_chunks_1)} 'hmm Ok!' chunks, and " +
                                           f"{len(self.ack_audio_chunks_2)} 'Sure!' chunks with voice model: {self.voice_model}")
                                self.greeting_generated = True
                                return True
                            else:
                                logger.info("Saved data is missing some audio chunks, will regenerate")
                                return False
                        else:
                            # Handle old format (just a list of chunks) - need to regenerate
                            logger.info("Saved data is in old format, will regenerate")
                            return False
                except Exception as e:
                    logger.error(f"Error loading greeting audio file: {str(e)}")
                    # If there's an error loading the file, we'll generate it again
            
            # If the file doesn't exist or couldn't be loaded, generate the greeting
            logger.info(f"Generating greeting: '{self.greeting_text}'")
            
            # Make sure we have a Deepgram connection
            if not self.deepgram_ws:
                logger.error("Cannot generate greeting: Deepgram WebSocket is not connected")
                return False
            
            # Create a temporary list to collect audio chunks
            temp_chunks = []
            
            # Set up a listener for Deepgram messages
            async def collect_greeting_audio():
                try:
                    # Send the greeting text to Deepgram
                    await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": self.greeting_text}))
                    logger.info(f"Sent greeting text to Deepgram TTS: '{self.greeting_text}'")
                    
                    # Flush to generate audio
                    await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
                    logger.info("Sent flush to Deepgram TTS for greeting")
                    
                    # Wait for and collect audio chunks
                    flushed_received = False
                    while not flushed_received:
                        message = await self.deepgram_ws.recv()
                        
                        if isinstance(message, bytes):
                            # Store the audio chunk
                            temp_chunks.append(message)
                            logger.debug(f"Collected greeting audio chunk: {len(message)} bytes")
                        elif isinstance(message, str):
                            try:
                                data = json.loads(message)
                                logger.debug(f"Received message from Deepgram: {data}")
                                
                                # Check for Flushed event
                                if data.get('type') == 'Flushed':
                                    logger.info("Received Flushed event from Deepgram - greeting generation complete")
                                    flushed_received = True
                            except:
                                pass
                    
                    return True
                except Exception as e:
                    logger.error(f"Error collecting greeting audio: {str(e)}")
                    return False
            
            # Helper function to collect audio for a specific text
            async def collect_audio_for_text(text):
                temp_chunks = []
                try:
                    # Send the text to Deepgram
                    await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": text}))
                    logger.info(f"Sent text to Deepgram TTS: '{text}'")
                    
                    # Flush to generate audio
                    await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
                    logger.info("Sent flush to Deepgram TTS")
                    
                    # Wait for and collect audio chunks
                    flushed_received = False
                    while not flushed_received:
                        message = await self.deepgram_ws.recv()
                        
                        if isinstance(message, bytes):
                            # Store the audio chunk
                            temp_chunks.append(message)
                            logger.debug(f"Collected audio chunk: {len(message)} bytes")
                        elif isinstance(message, str):
                            try:
                                data = json.loads(message)
                                logger.debug(f"Received message from Deepgram: {data}")
                                
                                # Check for Flushed event
                                if data.get('type') == 'Flushed':
                                    logger.info(f"Received Flushed event from Deepgram - '{text}' generation complete")
                                    flushed_received = True
                            except:
                                pass
                    
                    return temp_chunks, True
                except Exception as e:
                    logger.error(f"Error collecting audio for '{text}': {str(e)}")
                    return [], False
            
            # Collect the greeting audio
            greeting_chunks, greeting_success = await collect_audio_for_text(self.greeting_text)
            
            # Collect the "hmm Ok!" audio
            ack_chunks_1, ack_success_1 = await collect_audio_for_text(self.ack_text_1)
            
            # Collect the "Sure!" audio
            ack_chunks_2, ack_success_2 = await collect_audio_for_text(self.ack_text_2)
            
            # Check if all audio was generated successfully
            if greeting_success and ack_success_1 and ack_success_2 and greeting_chunks and ack_chunks_1 and ack_chunks_2:
                # Store the collected chunks
                self.greeting_audio_chunks = greeting_chunks
                self.ack_audio_chunks_1 = ack_chunks_1
                self.ack_audio_chunks_2 = ack_chunks_2
                logger.info(f"Generated {len(self.greeting_audio_chunks)} greeting audio chunks, " +
                           f"{len(self.ack_audio_chunks_1)} 'hmm Ok!' chunks, and " +
                           f"{len(self.ack_audio_chunks_2)} 'Sure!' chunks")
                
                # Save the chunks to a file for future use
                try:
                    # Make sure the directory exists
                    os.makedirs(os.path.dirname(self.greeting_audio_file), exist_ok=True)
                    
                    # Save the audio chunks along with the voice model
                    with open(self.greeting_audio_file, 'wb') as f:
                        # Save as a dictionary with voice model and all audio chunks
                        save_data = {
                            'voice_model': self.voice_model,
                            'greeting_chunks': self.greeting_audio_chunks,
                            'ack_chunks_1': self.ack_audio_chunks_1,
                            'ack_chunks_2': self.ack_audio_chunks_2
                        }
                        pickle.dump(save_data, f)
                    logger.info(f"Saved all audio chunks to {self.greeting_audio_file} with voice model: {self.voice_model}")
                except Exception as e:
                    logger.error(f"Error saving greeting audio file: {str(e)}")
                
                self.greeting_generated = True
                return True
            else:
                logger.error("Failed to generate greeting audio")
                return False
        except Exception as e:
            logger.error(f"Error generating greeting: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def _connect_to_deepgram(self, reconnect=False, twilio_websocket=None):
        """
        Connect to Deepgram TTS API.
        
        Args:
            reconnect: Whether this is a reconnection attempt
            twilio_websocket: The Twilio WebSocket connection (optional)
        
        Returns:
            WebSocket: The Deepgram WebSocket connection, or None if connection failed
        """
        # Log the type of the Twilio WebSocket
        if twilio_websocket is not None:
            logger.info(f"Twilio WebSocket type in _connect_to_deepgram: {type(twilio_websocket)}")
            logger.info(f"Twilio WebSocket has send_json: {hasattr(twilio_websocket, 'send_json')}")
        
        if not self.use_deepgram_tts:
            logger.info("Deepgram TTS connection disabled by configuration")
            return None
        
        # Use the deepgram lock to prevent concurrent connections/disconnections
        async with self.deepgram_lock:
            try:
                # Get Deepgram API key from environment
                api_key = os.environ.get("DEEPGRAM_API_KEY")
                if not api_key:
                    logger.error("Deepgram API key not found in environment variables")
                    return None
                
                # Initialize speaker for local audio playback if enabled and not reconnecting
                if self.use_local_playback and not reconnect and not self.speaker:
                    with self.speaker_lock:
                        self.speaker = Speaker()
                        self.speaker.start()
                        logger.info("Local audio playback initialized")
                elif not self.use_local_playback:
                    logger.info("Local audio playback is disabled by configuration")
                
                # Construct URL with model and audio format parameters
                model = self.voice_model  # Use the voice model from class property
                # Use mulaw encoding at 8kHz for Twilio compatibility
                url = f"wss://api.deepgram.com/v1/speak?encoding=mulaw&sample_rate=8000&model={model}"
                logger.info(f"Connecting to Deepgram TTS API with model: {model} (mulaw, 8kHz for Twilio compatibility)")
                
                # Connect to Deepgram
                deepgram_ws = await websockets.connect(
                    url, extra_headers={"Authorization": f"Token {api_key}"}
                )
                logger.info("Connected to Deepgram TTS API")
                
                # Start receiver task ONLY if the Twilio WebSocket is provided now
                # Otherwise, it will be started later in handle_media_stream
                if twilio_websocket:
                    logger.info("Twilio WebSocket provided, starting Deepgram receiver task (_receive_from_deepgram)")
                    asyncio.create_task(self._receive_from_deepgram(deepgram_ws, twilio_websocket))
                else:
                    logger.info("Twilio WebSocket not provided yet, Deepgram receiver task will start later.")

                return deepgram_ws
            except Exception as e:
                logger.error(f"Error connecting to Deepgram TTS: {str(e)}")
                if not reconnect and self.speaker:
                    with self.speaker_lock:
                        self.speaker.stop()
                        self.speaker = None
                return None
            
    async def _receive_from_deepgram(self, websocket, twilio_websocket=None):
        """
        Receive messages from Deepgram TTS.
        
        Args:
            websocket: The Deepgram WebSocket connection
            twilio_websocket: The Twilio WebSocket connection (optional)
        """
        try:
            # Log the type of the Twilio WebSocket
            if twilio_websocket is not None:
                logger.info(f"Twilio WebSocket type in _receive_from_deepgram: {type(twilio_websocket)}")
                logger.info(f"Twilio WebSocket has send_json: {hasattr(twilio_websocket, 'send_json')}")
            
            logger.info("Started receiving messages from Deepgram TTS")
            audio_chunks_received = 0
            total_audio_bytes = 0
            
            while True:
                message = await websocket.recv()
                
                if isinstance(message, str):
                    try:
                        data = json.loads(message)
                        logger.info(f"Received message from Deepgram: {data}")
                        
                        # Use playback lock for state changes
                        async with self.playback_lock:
                            # Handle Cleared event
                            if data.get('type') == 'Cleared':
                                logger.info("Received Cleared event from Deepgram - buffer has been cleared")
                                # Reset the playing flag if we're not about to send new text
                                if not self.is_playing_response:
                                    logger.info("Resetting playing flag")
                                    self.is_playing_response = False
                            
                            # Handle Flushed event
                            elif data.get('type') == 'Flushed':
                                sequence_id = data.get('sequence_id')
                                logger.info(f"Received Flushed event from Deepgram - sequence_id: {sequence_id}")
                                
                                # If this is the first sequence, mark it as complete
                                if self.is_first_sequence and sequence_id == self.current_sequence_id:
                                    logger.info("First sequence completed - enabling interruptions for future sequences")
                                    self.is_first_sequence = False
                    except:
                        logger.info(f"Received non-JSON message from Deepgram: {message}")
                elif isinstance(message, bytes):
                    audio_chunks_received += 1
                    total_audio_bytes += len(message)
                    logger.info(f"Received audio chunk #{audio_chunks_received} with {len(message)} bytes from Deepgram (total: {total_audio_bytes} bytes)")
                    
                    # Local audio playback is disabled in this version
                    # Just log that we received the audio chunk
                    logger.debug(f"Received audio chunk #{audio_chunks_received} (local playback disabled)")
                    
                    # Check if we're playing a response - use playback_lock
                    is_playing = False
                    has_stream_sid = False
                    
                    async with self.playback_lock:
                        is_playing = self.is_playing_response
                        has_stream_sid = bool(self.current_stream_sid)
                    
                    # Send audio to Twilio if we're playing a response and have a stream_sid
                    if twilio_websocket is not None and has_stream_sid:
                        try:
                            # Convert bytes to base64 string
                            audio_payload = base64.b64encode(message).decode('utf-8')
                            
                            # Create media message for Twilio
                            audio_delta = {
                                "event": "media",
                                "streamSid": self.current_stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            
                            # Send to Twilio using FastAPI WebSocket's send_json method
                            await twilio_websocket.send_json(audio_delta)
                            logger.info(f"Sent audio chunk #{audio_chunks_received} to Twilio")
                        except Exception as e:
                            logger.error(f"Error sending audio to Twilio: {str(e)}")
                            # Log more details about the WebSocket type to help diagnose issues
                            logger.error(f"WebSocket type: {type(twilio_websocket)}")
                            logger.error(f"WebSocket attributes: {dir(twilio_websocket)}")
                    elif not is_playing and not has_stream_sid:
                        logger.debug("Not sending audio chunk to Twilio - no active response or stream SID")
        except websockets.exceptions.ConnectionClosed:
            logger.info("Deepgram TTS connection closed")
        except Exception as e:
            logger.error(f"Error receiving from Deepgram: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
    def _setup_routes(self):
        """Set up FastAPI routes for Twilio Media Streams."""
        
        @self.app.get("/", response_class=HTMLResponse)
        async def index_page():
            return "<html><body><h1>Twilio Media Stream Server is running!</h1></body></html>"
            
        @self.app.api_route("/incoming-call", methods=["GET", "POST"])
        async def handle_incoming_call(request: Request):
            """Handle incoming call and return TwiML response to connect to Media Stream."""
            logger.info("Received incoming call request")
            response = VoiceResponse()
            
            # Add temporary greeting before connecting to Media Stream
            #response.say("Wait a moment, connecting", voice="alice", language="en-US")
            
            host = request.url.hostname
            connect = Connect()
            connect.stream(url=f'wss://{host}/media-stream')
            response.append(connect)
            response.say("Wait a moment, connecting", voice="en-US-Chirp3-HD-Kore", language="en-US")
            
            return HTMLResponse(content=str(response), media_type="application/xml")
            
        @self.app.websocket("/media-stream")
        async def handle_media_stream(websocket: WebSocket):
            """Handle WebSocket connections from Twilio."""
            await websocket.accept()
            logger.info("WebSocket connection accepted from Twilio")
            
            self.current_twilio_websocket = websocket
            
            try:
                # Get the running loop
                loop = asyncio.get_running_loop()
                
                # Create tasks for handling Twilio WebSocket
                twilio_receive_task = loop.create_task(
                    self._fastapi_handle_twilio_receive(websocket)
                )
                queue_to_twilio_task = loop.create_task(
                    self._fastapi_handle_queue_to_twilio(websocket)
                )
                
                # Wait for tasks to complete
                await asyncio.gather(twilio_receive_task, queue_to_twilio_task)
            except websockets.exceptions.ConnectionClosed as e:
                logger.error(f"WebSocket connection closed: {str(e)}")
            except Exception as e:
                logger.error(f"Error in WebSocket connection: {str(e)}")
            finally:
                logger.info("WebSocket connection closed")
                
                # Set stop_event and let the API thread handle the closing of the connections
                self.stop_event.set()
                logger.info("Set stop_event to signal API thread to close connections")
                
                # Give the API thread a moment to close the connections
                await asyncio.sleep(1.0)
                
                # Clear state
                self.current_twilio_websocket = None
                self.current_stream_sid = None
                
                if self.on_close_callback:
                    self.on_close_callback()
                
    # Removed _send_initial_conversation_item - not needed in experimental version
        
    async def _fastapi_handle_twilio_receive(self, websocket):
        """Handle messages from Twilio WebSocket."""
        try:
            async for message in websocket.iter_text():
                if self.stop_event.is_set():
                    break
                    
                data = json.loads(message)
                
                if data['event'] == 'media':
                    # Get timestamp and payload
                    timestamp = int(data['media'].get('timestamp', 0))
                    payload = data['media']['payload']
                    
                    # Update timestamp
                    self.latest_media_timestamp = timestamp
                    
                    # Put audio on queue for API thread
                    if payload and len(payload) > 0:
                        self.twilio_to_api_queue.put(payload)
                
                elif data['event'] == 'start':
                    # Store stream SID and set stream_started flag
                    self.current_stream_sid = data['start']['streamSid']
                    self.stream_started = True
                    logger.info(f"Stream started: {self.current_stream_sid}")
                    
                    # Send a Say command first
                    say_message = {
                        "event": "media",
                        "streamSid": self.current_stream_sid,
                        "media": {
                            "payload": "<Say voice=\"Polly.Alice\">Hold on connecting</Say>"
                        }
                    }
                    await websocket.send_json(say_message)
                    logger.info("Sent Say command to Twilio")
                    
                    # Small delay to ensure Say command is processed
                    await asyncio.sleep(0.5)
                    
                    # Send the pre-generated greeting if available
                    if self.greeting_generated and self.greeting_audio_chunks:
                        logger.info(f"Sending pre-generated greeting ({len(self.greeting_audio_chunks)} chunks)")
                        
                        # Send each audio chunk to Twilio
                        for chunk in self.greeting_audio_chunks:
                            # Convert bytes to base64 string
                            audio_payload = base64.b64encode(chunk).decode('utf-8')
                            
                            # Create media message for Twilio
                            audio_message = {
                                "event": "media",
                                "streamSid": self.current_stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            
                            # Send to Twilio
                            await websocket.send_json(audio_message)
                            
                            # Small delay to avoid overwhelming the connection
                            await asyncio.sleep(0.01)
                        
                        logger.info("Greeting audio sent to Twilio")
                
                elif data['event'] == 'mark':
                    # Handle mark callbacks
                    mark_name = data.get('mark', {}).get('name')
                    if self.on_mark_callback and mark_name:
                        self.on_mark_callback(mark_name)
                
                elif data['event'] == 'stop' or data['event'] == 'close':
                    logger.info("Stream stopped or closed")
                    # Don't set stop_event here, let the finally block in handle_media_stream do it
                    # after closing the connections properly
                    break
        except Exception as e:
            logger.error(f"Error handling Twilio messages: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            # Don't set stop_event here, let the finally block in handle_media_stream do it
            # after closing the connections properly

    async def _fastapi_handle_queue_to_twilio(self, websocket):
        """Handle messages from API to Twilio queue."""
        try:
            loop = asyncio.get_running_loop()
            
            while not self.stop_event.is_set():
                try:
                    # Use run_in_executor to get from queue without blocking the event loop
                    # Use a shorter timeout to reduce delay
                    message = await loop.run_in_executor(
                        None,
                        lambda: self.api_to_twilio_queue.get(block=True, timeout=0.01)
                    )
                    
                    # Handle different message types
                    if message['type'] == 'audio':
                        # Send audio to Twilio
                        if self.current_stream_sid:
                            audio_payload = base64.b64encode(message['payload']).decode('utf-8')
                            audio_message = {
                                "event": "media",
                                "streamSid": self.current_stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_message)
                            
                            # Send mark for tracking (only every 30th audio chunk to significantly reduce overhead)
                            self._audio_chunk_counter += 1
                            if self._audio_chunk_counter % 30 == 0:
                                await self._send_mark(websocket, self.current_stream_sid)
                    
                    elif message['type'] == 'clear':
                        # Send clear event to Twilio
                        if self.current_stream_sid:
                            clear_message = {
                                "event": "clear",
                                "streamSid": self.current_stream_sid
                            }
                            await websocket.send_json(clear_message)
                            logger.info("Sent clear event to Twilio")
                except queue.Empty:
                    # This is normal, just means no messages are available yet
                    await asyncio.sleep(0.01)
                except Exception as e:
                    logger.error(f"Error sending to Twilio: {str(e)}")
                    # Reduce sleep time to minimize delay
                    await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Error in queue to Twilio handler: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.stop_event.set()
            
    async def _send_session_update(self, openai_ws):
        """Send session update to OpenAI WebSocket."""
        session_update = {
            "type": "session.update",
            "session": {
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.7,
                    "prefix_padding_ms": 500,
                    "silence_duration_ms": 500,
                    "create_response": True
                },
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": self.config.voice,
                "instructions": self.system_prompt if self.system_prompt else self.config.get_system_prompt(),
                "modalities": ["text"],
                "temperature": 0.8,
            }
        }
        logger.debug(f'Sending session update to OpenAI: {json.dumps(session_update)}')
        await openai_ws.send(json.dumps(session_update))
        logger.info('Session update sent to OpenAI with modalities: ["text"')
        
    def start_stream_server(self, port=8080):
        """
        Start a FastAPI server for Twilio Media Streams.
        
        Args:
            port (int): The port to listen on
            
        Returns:
            bool: True if started successfully, False otherwise
        """
        try:
            # Set the port
            self.port = port
            
            # Create a thread to run the server
            self.server_thread = threading.Thread(
                target=self._run_server,
                args=(port,),
                daemon=True
            )
            
            # Start the thread
            self.server_thread.start()
            
            # Wait for the server to start
            time.sleep(1)
            
            # Set the flag
            self.server_running = True
            
            return True
        except Exception as e:
            logger.error(f"Error starting stream server: {str(e)}")
            return False
            
    def _run_server(self, port):
        """Run the FastAPI server."""
        try:
            # Run the server
            uvicorn.run(
                self.app,
                host="0.0.0.0",
                port=port,
                log_level="error"
            )
        except Exception as e:
            logger.error(f"Error running server: {str(e)}")
            
    def stop_stream_server(self):
        """Stop the FastAPI server."""
        # The server will stop when the thread exits
        self.server_running = False
        
    def make_simple_call(self, message):
        """
        Make a simple outbound call with a text message.
        
        Args:
            message (str): The message to say
            
        Returns:
            twilio.rest.api.v2010.account.call.CallInstance: The call instance
        """
        try:
            # Create TwiML to say the message
            response = VoiceResponse()
            response.say(message, voice=self.config.voice)
            response.hangup()
            
            # Make the call with the TwiML
            return self.make_call(twiml_string=str(response))
            
        except Exception as e:
            logger.error(f"Error making simple call: {str(e)}")
            raise
        
    def make_call(self, twiml_string=None, stream_url=None):
        """
        Make an outbound call with TwiML.
        
        Args:
            twiml_string (str, optional): TwiML instructions as a string
            stream_url (str, optional): URL for Media Streams
            
        Returns:
            twilio.rest.api.v2010.account.call.CallInstance: The call instance
        """
        try:
            # Create TwiML with Media Streams if stream_url is provided
            if stream_url:
                response = VoiceResponse()
                # Add a brief pause to ensure the call connects properly
                response.pause(length=1)
                # Connect to Media Streams
                connect = Connect()
                connect.stream(url=stream_url)
                response.append(connect)
                twiml_string = str(response)
            # Create TwiML if not provided
            elif not twiml_string:
                response = VoiceResponse()
                response.say("Hello, this is a call from the outbound caller application.")
                response.hangup()
                twiml_string = str(response)
                
            # Make the call
            call_params = {
                'to': self.config.phone_number,
                'from_': self.config.twilio_phone_number,
                'twiml': twiml_string,
                'timeout': 60
            }
            
            # Add record parameter if enabled
            if self.config.save_recording:
                call_params['record'] = True
                
            # Make the call
            call = self.client.calls.create(**call_params)
            
            # Save the call
            self.call = call
            
            return call
        except Exception as e:
            logger.error(f"Error making call: {str(e)}")
            raise
            
    def get_call_status(self):
        """
        Get the status of the current call.
        
        Returns:
            str: The call status, or None if no call
        """
        try:
            if not self.call:
                return None
                
            # Fetch the call to get the latest status
            call = self.client.calls(self.call.sid).fetch()
            logger.debug(f"Call status: {call.status}")
            return call.status
            
        except Exception as e:
            logger.error(f"Error getting call status: {str(e)}")
            return None
            
    async def connect_to_apis(self, twilio_websocket=None):
        """
        Connect to Deepgram and OpenAI APIs.
        
        Args:
            twilio_websocket: The Twilio WebSocket connection (if available)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Log the type of the Twilio WebSocket
            if twilio_websocket is not None:
                logger.info(f"Twilio WebSocket type in connect_to_apis: {type(twilio_websocket)}")
                logger.info(f"Twilio WebSocket has send_json: {hasattr(twilio_websocket, 'send_json')}")
            
            # Get the current event loop
            loop = asyncio.get_running_loop()
            
            # Connect to Deepgram TTS first (if enabled)
            logger.info("Just before Deepgram")
            if self.use_deepgram_tts and not self.deepgram_ws:
                logger.info("Connecting to Deepgram TTS API...")
                self.deepgram_ws = await self._connect_to_deepgram(twilio_websocket=twilio_websocket)
                if not self.deepgram_ws:
                    logger.warning("Failed to connect to Deepgram TTS, continuing without local audio playback")
                # No test message - removed to avoid unnecessary audio
            
            # Connect to OpenAI if not already connected
            if not self.openai_ws:
                logger.info("Connecting to OpenAI Realtime API...")
                openai_url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
                openai_headers = {
                    "Authorization": f"Bearer {self.config.openai_api_key}",
                    "OpenAI-Beta": "realtime=v1",
                    "modalities": ["text"]  # Enable both text and audio modalities
                }
                
                self.openai_ws = await websockets.connect(
                    openai_url,
                    extra_headers=openai_headers
                )
                logger.info("Connected to OpenAI Realtime API")
                
                # Send session update
                await self._send_session_update(self.openai_ws)
            
            # If Twilio WebSocket is provided *now*, start the receiver task for OpenAI
            # Otherwise, this task will be started later in handle_media_stream
            if twilio_websocket and self.openai_ws:
                logger.info("Twilio WebSocket provided, starting OpenAI receiver task (_send_to_twilio)")
                # Start receiver task for OpenAI with the Twilio WebSocket in the same event loop
                loop.create_task(self._send_to_twilio(twilio_websocket, self.openai_ws))
            else:
                logger.info("Twilio WebSocket not provided yet, OpenAI receiver task will start later.")
            
            return True
        except Exception as e:
            logger.error(f"Error connecting to APIs: {str(e)}")
            return False
    
    def get_websocket_url(self, stream_url_arg, use_ngrok_arg, port_arg):
        """Get WebSocket URL for Twilio Media Streams."""
        try:
            # If stream URL is provided, use it
            if stream_url_arg:
                stream_url = stream_url_arg
                
                # Ensure the URL has the correct protocol
                if not stream_url.startswith('http://') and not stream_url.startswith('https://') and \
                   not stream_url.startswith('ws://') and not stream_url.startswith('wss://'):
                    stream_url = f"https://{stream_url}"
                
                # Convert HTTP to WebSocket if needed
                if stream_url.startswith('http://'):
                    stream_url = stream_url.replace('http://', 'ws://')
                elif stream_url.startswith('https://'):
                    stream_url = stream_url.replace('https://', 'wss://')
                
                # Make sure the URL ends with /media-stream
                if not stream_url.endswith('/media-stream'):
                    stream_url = f"{stream_url}/media-stream"
                
                return stream_url
            
            # If ngrok is enabled, get the URL from the ngrok API
            elif use_ngrok_arg:
                try:
                    response = requests.get("http://localhost:4040/api/tunnels")
                    tunnels = response.json()["tunnels"]
                    for tunnel in tunnels:
                        if tunnel["proto"] == "https":
                            ngrok_url = tunnel["public_url"]
                            return f"{ngrok_url}/media-stream"
                    
                    logger.error("No HTTPS tunnel found in ngrok")
                    return None
                except Exception as e:
                    logger.error(f"Error getting ngrok URL: {str(e)}")
                    return None
            
            # Otherwise, use the local IP address
            else:
                ip_address = socket.gethostbyname(socket.gethostname())
                return f"wss://{ip_address}:{port_arg}/media-stream"
        except Exception as e:
            logger.error(f"Error getting WebSocket URL: {str(e)}")
            return None
            
    async def make_call_with_media_streams(self, stream_url=None): # Make async
        """
        Make an outbound call with Media Streams. Connects to APIs first.
        
        Args:
            stream_url (str, optional): The URL for the Media Streams WebSocket
                If not provided, a local server will be started
                
        Returns:
            twilio.rest.api.v2010.account.call.CallInstance: The call instance
        """
        try:
            # If no stream URL provided, use a local server
            if not stream_url:
                # Get the local IP address
                ip_address = socket.gethostbyname(socket.gethostname())
                
                # Create a stream URL
                stream_url = f"wss://{ip_address}:{self.port}/media-stream"
                
                logger.warning(f"No stream URL provided. Using local URL: {stream_url}")
                logger.warning("This will only work if Twilio can reach this URL.")
                logger.warning("For production, use a public URL with --stream-url.")
            else:
                # Ensure the URL has the correct protocol
                if not stream_url.startswith('http://') and not stream_url.startswith('https://') and not stream_url.startswith('ws://') and not stream_url.startswith('wss://'):
                    stream_url = f"https://{stream_url}"
                
                # Convert HTTP to WebSocket if needed
                if stream_url.startswith('http://'):
                    stream_url = stream_url.replace('http://', 'ws://')
                elif stream_url.startswith('https://'):
                    stream_url = stream_url.replace('https://', 'wss://')
                elif not stream_url.startswith('ws://') and not stream_url.startswith('wss://'):
                    stream_url = f"wss://{stream_url}"
                
                # Make sure the URL ends with /media-stream
                if not stream_url.endswith('/media-stream'):
                    stream_url = f"{stream_url}/media-stream"
            
            # Save the stream URL
            self.stream_url = stream_url
            logger.info(f"Using Media Streams URL: {stream_url}")
            
            # Start the server regardless of whether a stream URL was provided
            if not self.start_stream_server(port=self.port):
                raise Exception("Failed to start Media Streams server")

            # Get the current event loop (or create one if needed)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                
            # Connect to Deepgram and OpenAI APIs *before* making the call
            logger.info("Connecting to Deepgram and OpenAI APIs before placing the call...")
            connection_success = await self.connect_to_apis() # Pass no twilio_websocket here
            
            if not connection_success:
                 raise Exception("Failed to connect to Deepgram or OpenAI APIs before making call")
            logger.info("Successfully connected to APIs.")

            # Create TwiML with Media Streams
            response = VoiceResponse()
            
            # Add a brief pause to ensure the call connects properly
            response.pause(length=1)
            
            # Connect to Media Streams
            connect = Connect()
            connect.stream(url=stream_url)
            response.append(connect)
            
            # Make the call
            call_params = {
                'to': self.config.phone_number,
                'from_': self.config.twilio_phone_number,
                'twiml': str(response),
                'timeout': 60
            }
            
            # Add record parameter if enabled
            if self.config.save_recording:
                call_params['record'] = True
                
            # Make the call
            call = self.client.calls.create(**call_params)
            
            # Save the call
            self.call = call
            
            return call
        except Exception as e:
            logger.error(f"Error making call with Media Streams: {str(e)}")
            raise
            
    def end_call(self):
        """End the current call."""
        try:
            if self.call:
                # Update the call to end it
                self.client.calls(self.call.sid).update(status='completed')
                logger.info(f"Ended call with SID: {self.call.sid}")
                
                # Clear the call
                self.call = None
        except Exception as e:
            logger.error(f"Error ending call: {str(e)}")
            
    def get_recording_url(self):
        """
        Get the URL for the call recording.
        
        Returns:
            str: The recording URL, or None if no recording
        """
        try:
            if not self.call:
                return None
                
            # Wait a moment for the recording to be processed
            time.sleep(2)
            
            # Get the recordings for the call
            recordings = self.client.recordings.list(call_sid=self.call.sid)
            
            if recordings:
                # Get the most recent recording
                recording = recordings[0]
                recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.config.twilio_account_sid}/Recordings/{recording.sid}.mp3"
                return recording_url
            else:
                logger.warning("No recording found for the call")
                return None
                
        except Exception as e:
            logger.error(f"Error getting recording URL: {str(e)}")
            return None
            # Removed set_audio_callback method - VAD is fully handled by OpenAI
            
        # Removed set_transcription_callback - not needed in experimental version
        
    def set_mark_callback(self, callback):
        """
        Set the callback for mark events.
        
        Args:
            callback (callable): The callback function that takes a mark name
        """
        self.on_mark_callback = callback
        
    def set_close_callback(self, callback):
        """
        Set the callback for connection close events.
        
        Args:
            callback (callable): The callback function with no arguments
        """
        self.on_close_callback = callback
        # Removed compatibility methods that aren't used in the experimental implementation
        pass
        
    # Removed set_greeting_audio - not needed in experimental version
        
    # Removed compatibility methods that aren't used in the experimental implementation

    async def _maybe_send_initial_hello(self, openai_ws):
        """Send the initial 'Hello' message if conditions are met."""
        # Check conditions with proper locking
        should_send = False
        
        # Use playback_lock for accessing shared state
        async with self.playback_lock:
            should_send = (self.stream_started and self.openai_session_updated and not self.initial_hello_sent)
            
        if should_send:
            logger.info("Conditions met: Sending initial 'Hello' message to OpenAI.")
            try:
                # Use openai_lock for OpenAI WebSocket operations
                async with self.openai_lock:
                    # Create a conversation item to trigger the flow
                    conversation_item = {
                        "event_id": f"event_{int(time.time())}",
                        "type": "conversation.item.create",
                        "previous_item_id": None,
                        "item": {
                            "id": f"msg_{int(time.time())}",
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Hello, I'm calling to check if everything is alright."
                                }
                            ]
                        }
                    }
                    
                    # Log the full message for debugging
                    logger.info(f"Sending initial conversation item: {json.dumps(conversation_item)}")
                    await openai_ws.send(json.dumps(conversation_item))
                    
                    # Wait a moment for the item to be processed
                    await asyncio.sleep(0.5)
                    
                    # Also trigger the first response creation
                    response_create = {"type": "response.create"}
                    logger.info(f"Sending response.create: {json.dumps(response_create)}")
                    await openai_ws.send(json.dumps(response_create))
                    logger.info("Sent response.create after initial Hello")
                
                # Set the flag with lock protection
                async with self.playback_lock:
                    self.initial_hello_sent = True  # Mark as sent
                    logger.info("Sent initial conversation item with text 'Hello'")
            except Exception as e:
                logger.error(f"Error sending initial 'Hello' message: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            # Get current state for debugging
            async with self.playback_lock:
                has_stream_sid = bool(self.current_stream_sid)
                stream_started = self.stream_started
                session_updated = self.openai_session_updated
                hello_sent = self.initial_hello_sent
                
            logger.info(f"Conditions not met for initial hello: stream_sid={has_stream_sid}, stream_started={stream_started}, session_updated={session_updated}, hello_sent={hello_sent}")
                
    async def _send_to_twilio(self, websocket, openai_ws):
        """Receive events from the OpenAI Realtime API, send audio back to Twilio, and collect text responses."""
        try:
            async for openai_message in openai_ws:
                response = json.loads(openai_message)
                event_type = response.get('type', '')
                
                # Log ALL events for debugging
                if event_type == 'response.text.delta' and 'delta' in response:
                    # Only log response.text.delta events with delta field value
                    delta_value = response['delta']
                    logger.debug(f"Received OpenAI event: {event_type} - Delta: {delta_value}")
                else:
                    logger.debug(f"Received OpenAI event: {event_type}")
                
                # Log important events with more detail
                if event_type in [
                    'response.content.done', 'rate_limits.updated', 'response.done',
                    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
                    'input_audio_buffer.speech_started', 'response.create', 'session.created',
                    'session.updated'
                ]:
                    logger.debug(f"Received important event: {event_type}")
                
                # Handle session updated event
                if event_type == 'session.updated':
                    logger.info("OpenAI session updated.")
                    # Update the session flag with lock protection
                    async with self.playback_lock:
                        self.openai_session_updated = True
                    # Check if we can send the initial hello now
                    await self._maybe_send_initial_hello(openai_ws)
                
                # Handle audio delta events - send audio to Twilio
                if event_type == 'response.audio.delta' and 'delta' in response:
                    # Get stream_sid safely with lock
                    current_stream_sid = None
                    async with self.playback_lock:
                        current_stream_sid = self.current_stream_sid
                    
                    if current_stream_sid:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": current_stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        
                        # Only send to Twilio if websocket is available
                        if websocket is not None:
                            try:
                                await websocket.send_json(audio_delta)
                                
                                # Update response timestamp with lock protection
                                async with self.playback_lock:
                                    if self.response_start_timestamp_twilio is None:
                                        self.response_start_timestamp_twilio = self.latest_media_timestamp
                                        logger.debug(f"Setting start timestamp for new response: {self.response_start_timestamp_twilio}ms")
                                    
                                    # Update last_assistant_item safely
                                    if response.get('item_id'):
                                        self.last_assistant_item = response['item_id']
                                
                                await self._send_mark(websocket, current_stream_sid)
                            except Exception as e:
                                logger.error(f"Error sending audio to Twilio: {str(e)}")
                        else:
                            logger.warning("Cannot send audio to Twilio: WebSocket is None")
                    else:
                        logger.warning("Cannot send audio to Twilio: Stream SID is None")
                
                # Handle response.text.delta event (EXPERIMENTAL)
                elif event_type == 'response.text.delta' and 'delta' in response:
                    # Extract the delta field from the JSON response
                    delta_text = response['delta']
                    # Print the delta field value with color
                    print(f"\033[1;35m{delta_text}\033[0m", end="", flush=True)  # Magenta text
                    
                    # Log the delta value
                    logger.info(f"DELTA TEXT: {delta_text}")
                    
                    # Accumulate text with lock protection
                    async with self.text_response_lock:
                        self.current_text_response += delta_text
                        accumulated_text = self.current_text_response
                        # Count words by splitting on whitespace
                        word_count = len(accumulated_text.split())
                    
                    # Only send accumulated text to Deepgram if:
                    # 1. We have 50+ words AND receive punctuation
                    if self.deepgram_ws and any(char in delta_text for char in [',', '.', '?', '!', ';', ':']) and word_count >= 50:
                        # Send the accumulated text
                        await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": accumulated_text}))
                        logger.info(f"Sent accumulated text to Deepgram TTS: '{accumulated_text}' (Word count: {word_count})")
                        
                        # Set playing flag
                        async with self.playback_lock:
                            self.is_playing_response = True
                        
                        # Flush to generate audio
                        await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
                        logger.info("Sent flush to Deepgram TTS")
                        
                        # Reset accumulated text after sending
                        async with self.text_response_lock:
                            self.current_text_response = ""
                
                # Handle content done event
                elif event_type == 'response.content.done':
                    logger.info("OPENAI CONTENT DONE")
                    print("\n\033[1;33m[TEXT RESPONSE COMPLETE]\033[0m", flush=True)  # Yellow text
                
                # Handle text.done event - print complete text (EXPERIMENTAL)
                elif event_type == 'response.text.done':
                    # Log the raw event for debugging
                    logger.info(f"RECEIVED TEXT.DONE EVENT: {json.dumps(response)}")
                    
                    # Extract fields from the response.text.done event
                    event_id = response.get('event_id', 'unknown')
                    response_id = response.get('response_id', 'unknown')
                    item_id = response.get('item_id', 'unknown')
                    output_index = response.get('output_index', 0)
                    content_index = response.get('content_index', 0)
                    
                    # Get current text response with lock protection
                    async with self.text_response_lock:
                        final_text = response.get('text', self.current_text_response)
                        accumulated_text = self.current_text_response
                        word_count = len(accumulated_text.split())
                    
                    # Log the complete response with all fields
                    logger.info(f"OPENAI TEXT DONE - Event ID: {event_id}, Response ID: {response_id}, Item ID: {item_id}")
                    logger.info(f"Output Index: {output_index}, Content Index: {content_index}")
                    logger.info(f"COMPLETE TEXT RESPONSE: {final_text} (Word count: {word_count})")
                    
                    # Print the response in a formatted way
                    print("\n\033[1;31m[COMPLETE TEXT RESPONSE]\033[0m", flush=True)  # Red text
                    print(f"\033[1;31mEvent ID: {event_id}\033[0m", flush=True)
                    print(f"\033[1;31mResponse ID: {response_id}\033[0m", flush=True)
                    print(f"\033[1;31mItem ID: {item_id}\033[0m", flush=True)
                    print(f"\033[1;31mText: {final_text}\033[0m", flush=True)
                    print(f"\033[1;31mWord Count: {word_count}\033[0m", flush=True)
                    print("\033[1;31m[END OF COMPLETE TEXT RESPONSE]\033[0m", flush=True)
                    
                    # Also print to stderr to ensure it's visible
                    print(f"COMPLETE TEXT RESPONSE: {final_text} (Word count: {word_count})", file=sys.stderr, flush=True)
                    
                    # Check if deepgram is available
                    should_send_to_tts = False
                    is_playing = False
                    current_deepgram_ws = None
                    
                    # Get current state with lock protection
                    async with self.deepgram_lock:
                        current_deepgram_ws = self.deepgram_ws
                    
                    async with self.playback_lock:
                        is_playing = self.is_playing_response
                        should_send_to_tts = bool(current_deepgram_ws and self.use_deepgram_tts)
                    
                    # Send any accumulated text and flush to Deepgram TTS
                    # For text.done, we always send the text regardless of word count
                    if should_send_to_tts:
                        try:
                            # If there's accumulated text that hasn't been sent yet, send it
                            if accumulated_text.strip():
                                async with self.deepgram_lock:
                                    await self.deepgram_ws.send(json.dumps({"type": "Speak", "text": final_text}))
                                    logger.info(f"Sent final text to Deepgram TTS: '{final_text}' (Word count: {word_count})")
                            
                            # Set playing flag
                            async with self.playback_lock:
                                self.is_playing_response = True
                            
                            # Send flush with lock protection
                            async with self.deepgram_lock:
                                # Flush to generate audio
                                await self.deepgram_ws.send(json.dumps({"type": "Flush"}))
                                logger.info("Sent final flush to Deepgram TTS")
                                
                                # Update and send sequence_id with lock protection
                                async with self.playback_lock:
                                    self.current_sequence_id = (self.current_sequence_id or 0) + 1
                                    current_sequence_id = self.current_sequence_id
                                
                                # This is now handled by the code we added above
                        except Exception as e:
                            logger.error(f"Error sending to Deepgram TTS: {str(e)}")
                            # Reset the playing flag if we fail
                            async with self.playback_lock:
                                self.is_playing_response = False
                    
                    # Reset for next response with lock protection
                    async with self.text_response_lock:
                        self.current_text_response = ""
                
                # Handle transcription from OpenAI
                elif event_type == 'input_audio_buffer.transcription' and 'text' in response:
                    transcription = response['text']
                    logger.info(f"Received transcription from OpenAI: '{transcription}'")
                    
                    # Just log the transcription - no callback needed in experimental version
                    logger.info(f"Received transcription from OpenAI (no callback used): '{transcription}'")
                
                # Handle transcription delta from OpenAI (partial transcription)
                elif event_type == 'conversation.item.input_audio_transcription.delta':
                    try:
                        # Check if response is a dictionary
                        if isinstance(response, dict) and 'delta' in response:
                            delta = response.get('delta', {})
                            if isinstance(delta, dict):
                                delta_text = delta.get('text', '')
                                if delta_text:
                                    # Log the transcription delta
                                    logger.debug(f"Transcription delta: '{delta_text}'")
                                    # No console printing as requested
                            else:
                                # Handle case where delta is not a dictionary
                                logger.debug(f"Transcription delta received but delta is not a dictionary: {delta}")
                        else:
                            # Handle case where response is not a dictionary or doesn't have delta
                            logger.debug(f"Transcription delta event received but format is unexpected: {type(response)}")
                    except Exception as e:
                        logger.error(f"Error processing transcription delta: {str(e)}")
                
                # Handle completed transcription from OpenAI
                elif event_type == 'conversation.item.input_audio_transcription.completed' and 'transcript' in response:
                    transcript = response['transcript']
                    item_id = response.get('item_id', '<unknown>')
                    
                    # Log the complete transcript
                    logger.info(f"COMPLETE TRANSCRIPT: '{transcript}'")
                    
                    # No console printing as requested
                
                # Handle speech started event for interruption (OpenAI's VAD)
                elif event_type == 'input_audio_buffer.speech_started':
                    # Check if we have a last_assistant_item with lock protection
                    has_last_item = False
                    async with self.playback_lock:
                        has_last_item = bool(self.last_assistant_item)
                    
                    logger.debug("OpenAI VAD: Speech started detected.")
                    if has_last_item:
                        logger.debug(f"OpenAI VAD: Interrupting response")
                        await self._handle_speech_started_event(websocket, openai_ws)
                        
        except Exception as e:
            logger.error(f"Error in send_to_twilio: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
    async def _handle_speech_started_event(self, websocket, openai_ws):
        """Handle interruption when the caller's speech starts (detected by OpenAI's VAD)."""
        logger.debug("Handling OpenAI VAD speech started event.")
        
        # Check conditions with proper lock protection
        should_stop_playback = False
        has_deepgram_ws = False
        is_first_seq = False
        current_stream_sid = None
        
        # Get current state safely
        async with self.playback_lock:
            should_stop_playback = self.is_playing_response and not self.is_first_sequence
            is_first_seq = self.is_first_sequence
            current_stream_sid = self.current_stream_sid
        
        async with self.deepgram_lock:
            has_deepgram_ws = self.deepgram_ws is not None
        
        # Only stop playback if conditions are met
        if should_stop_playback and has_deepgram_ws:
            logger.info("User started speaking - stopping Deepgram playback")
            await self._stop_deepgram_playback()
            
            # Reset state variables with lock protection
            async with self.playback_lock:
                self.mark_queue.clear()
                self.last_assistant_item = None
                self.response_start_timestamp_twilio = None
            
            # Send clear event to Twilio if we have a stream_sid
            if current_stream_sid and websocket:
                try:
                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": current_stream_sid
                    })
                    logger.info("Sent clear event to Twilio")
                    
                    # Randomly choose between the two acknowledgment responses
                    import random
                    ack_chunks = self.ack_audio_chunks_1 if random.random() < 0.5 else self.ack_audio_chunks_2
                    ack_text = self.ack_text_1 if ack_chunks == self.ack_audio_chunks_1 else self.ack_text_2
                    
                    if ack_chunks:
                        logger.info(f"Sending acknowledgment response: '{ack_text}'")
                        
                        # Send each audio chunk to Twilio
                        for chunk in ack_chunks:
                            # Convert bytes to base64 string
                            audio_payload = base64.b64encode(chunk).decode('utf-8')
                            
                            # Create media message for Twilio
                            audio_message = {
                                "event": "media",
                                "streamSid": current_stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            
                            # Send to Twilio
                            await websocket.send_json(audio_message)
                            
                            # Small delay to avoid overwhelming the connection
                            await asyncio.sleep(0.01)
                        
                        logger.info(f"Sent acknowledgment audio to Twilio: '{ack_text}'")
                except Exception as e:
                    logger.error(f"Error sending clear event or acknowledgment to Twilio: {str(e)}")
            
            logger.info("Reset state after speech detection")
        elif is_first_seq:
            logger.info("Ignoring interruption during first sequence (not interruptible)")
            
    async def _receive_from_twilio(self, websocket, openai_ws):
        """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
        try:
            if websocket is None:
                logger.warning("WebSocket is None, cannot receive from Twilio")
                return
                
            async for message in websocket.iter_text():
                data = json.loads(message)
                
                if data['event'] == 'media':
                    # Update media timestamp with proper locking
                    async with self.playback_lock:
                        self.latest_media_timestamp = int(data['media'].get('timestamp', 0))
                    
                    # Get the payload
                    payload = data['media']['payload']
                    
                    # Skip empty payloads (0 byte audio chunks)
                    if payload and len(payload) > 0:
                        try:
                            # Validate base64 payload by attempting to decode it
                            # Add padding if needed
                            padding_needed = len(payload) % 4
                            if padding_needed:
                                payload += '=' * (4 - padding_needed)
                                
                            # Try to decode to validate
                            audio_data = base64.b64decode(payload)
                            
                            # Skip if the decoded audio is empty or too small
                            if not audio_data or len(audio_data) < 10:  # Minimum size threshold
                                # Skip this chunk but don't log to reduce noise
                                continue
                                
                            # Forward to OpenAI with lock protection
                            async with self.openai_lock:
                                if openai_ws and openai_ws.open:
                                    # Log the audio data size being sent to OpenAI
                                    logger.info(f"Sending audio to OpenAI: {len(audio_data)} bytes")
                                    
                                    audio_append = {
                                        "type": "input_audio_buffer.append",
                                        "audio": payload
                                    }
                                    await openai_ws.send(json.dumps(audio_append))
                                    
                                    # Create a conversation item after sending enough audio
                                    # This helps ensure OpenAI creates a response
                                    if not self.initial_hello_sent and len(audio_data) > 1000:
                                        # Trigger a response creation
                                        await openai_ws.send(json.dumps({"type": "response.create"}))
                                        logger.info("Sent response.create to OpenAI after receiving significant audio")
                                else:
                                    logger.warning("Cannot send audio to OpenAI: WebSocket is closed or None")
                            
                            # No local VAD - Voice Activity Detection is fully handled by OpenAI
                                
                        except Exception as e:
                            # Skip invalid base64 data
                            logger.debug(f"Skipping invalid audio data: {str(e)}")
                            
                elif data['event'] == 'start':
                    # Update stream state with proper locking
                    async with self.playback_lock:
                        self.current_stream_sid = data['start']['streamSid']
                        self.response_start_timestamp_twilio = None
                        self.latest_media_timestamp = 0
                        self.last_assistant_item = None
                        stream_sid_value = self.current_stream_sid
                    
                    logger.info(f"Stream started: {stream_sid_value}")
                    
                    # Send a Say command first
                    say_message = {
                        "event": "media",
                        "streamSid": stream_sid_value,
                        "media": {
                            "payload": "<Say voice=\"Polly.Alice\">Hold on connecting</Say>"
                        }
                    }
                    await websocket.send_json(say_message)
                    logger.info("Sent Say command to Twilio")
                    
                    # Small delay to ensure Say command is processed
                    await asyncio.sleep(0.5)
                    
                    # Send the pre-generated greeting if available
                    if self.greeting_generated and self.greeting_audio_chunks:
                        logger.info(f"Sending pre-generated greeting ({len(self.greeting_audio_chunks)} chunks)")
                        
                        # Send each audio chunk to Twilio
                        for chunk in self.greeting_audio_chunks:
                            # Convert bytes to base64 string
                            audio_payload = base64.b64encode(chunk).decode('utf-8')
                            
                            # Create media message for Twilio
                            audio_message = {
                                "event": "media",
                                "streamSid": stream_sid_value,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            
                            # Send to Twilio
                            await websocket.send_json(audio_message)
                            
                            # Small delay to avoid overwhelming the connection
                            await asyncio.sleep(0.01)
                        
                        logger.info("Greeting audio sent to Twilio")
                    
                    # Check if we can send the initial hello now that the stream has started
                    await self._maybe_send_initial_hello(openai_ws)
                    
                elif data['event'] == 'mark':
                    # Handle marks with proper locking
                    should_pop = False
                    async with self.playback_lock:
                        if self.mark_queue:
                            self.mark_queue.pop(0)
                            should_pop = True
                    
                    # Call the mark callback if set (outside the lock)
                    mark_name = data.get('mark', {}).get('name')
                    if should_pop and self.on_mark_callback and mark_name:
                        try:
                            self.on_mark_callback(mark_name)
                        except Exception as e:
                            logger.error(f"Error in mark callback: {str(e)}")
                            
                elif data['event'] == 'close':
                    logger.info("Stream closed")
                    # Reset stream_sid with lock protection
                    async with self.playback_lock:
                        self.current_stream_sid = None
                    break
                    
        except Exception as e:
            logger.error(f"Error in receive_from_twilio: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
    async def _send_mark(self, connection, stream_sid):
        """Send a mark event to Twilio."""
        if connection and stream_sid:
            try:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                
                # Append to mark queue with lock protection
                async with self.playback_lock:
                    self.mark_queue.append('responsePart')
                    
            except Exception as e:
                logger.error(f"Error sending mark event: {str(e)}")