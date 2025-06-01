#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Conversation Manager Module

This module manages the conversation flow, integrating LLM and Twilio handler components
for the OpenDeep implementation that uses Twilio + OpenAI Realtime + Deepgram TTS.
"""

import logging
import time
import json
import threading
import queue
import base64
from datetime import datetime

# Get the logger
logger = logging.getLogger(__name__)


class ConversationManager:
    """Manager for conversation flow."""
    
    def __init__(self, config, llm_handler, storage_manager, twilio_handler=None):
        """
        Initialize the conversation manager.
        
        Args:
            config (Config): The application configuration
            llm_handler (LLMHandler): The LLM handler
            storage_manager (StorageManager): The storage manager
            twilio_handler (TwilioHandler, optional): The Twilio handler for Media Streams
        """
        self.config = config
        self.llm = llm_handler
        self.storage = storage_manager
        self.twilio = twilio_handler
        
        # Conversation state
        self.is_active = False
        self.transcript = []
        self.start_time = None
        self.end_time = None
        self.is_user_speaking = False
        self.is_assistant_speaking = False
        
        # Media Streams integration
        if self.twilio:
            # Set callbacks
            if hasattr(self.twilio, 'set_mark_callback'):
                self.twilio.set_mark_callback(self.handle_stream_mark)
            if hasattr(self.twilio, 'set_close_callback'):
                self.twilio.set_close_callback(self.handle_stream_close)
        
        logger.debug("Conversation manager initialized for OpenDeep")
        
    def start_conversation(self):
        """
        Start the conversation.
        
        Returns:
            bytes: The greeting audio to be played, or None if failed
        """
        try:
            logger.info("Starting conversation")
            self.is_active = True
            self.start_time = datetime.now()
            self.transcript = []
            
            # Generate initial greeting
            initial_greeting = self._generate_greeting()
            
            # Add greeting to transcript
            self._add_to_transcript("assistant", initial_greeting)
            
            # Since we're using OpenAI Realtime with Deepgram TTS,
            # we don't need to convert the greeting to speech here.
            # Just return the text to be passed to the Twilio handler.
            logger.info(f"Initial greeting: {initial_greeting}")
            return initial_greeting
            
        except Exception as e:
            logger.error(f"Error starting conversation: {str(e)}")
            self.is_active = False
            return None
            
    def end_conversation(self):
        """
        End the conversation.
        
        Returns:
            str: The farewell message, or None if failed
        """
        try:
            if not self.is_active:
                logger.warning("Conversation is not active")
                return None
                
            logger.info("Ending conversation")
            self.is_active = False
            self.end_time = datetime.now()
            
            # Generate farewell message
            farewell = self._generate_farewell()
            
            # Add farewell to transcript
            self._add_to_transcript("assistant", farewell)
            
            # Save conversation data
            self._save_conversation_data()
            
            # End the call if we have a Twilio handler
            if self.twilio and self.twilio.call:
                self.twilio.end_call()
            
            # Return the farewell message
            logger.info(f"Farewell message: {farewell}")
            return farewell
            
        except Exception as e:
            logger.error(f"Error ending conversation: {str(e)}")
            self.is_active = False
            return None
            
    def handle_stream_mark(self, mark_name):
        """
        Handle mark events from Twilio Media Streams.
        
        Args:
            mark_name (str): The mark name
        """
        try:
            logger.debug(f"Received mark: {mark_name}")
            
            if mark_name == "greeting_complete":
                self.is_assistant_speaking = False
            elif mark_name == "response_complete":
                self.is_assistant_speaking = False
            elif mark_name == "responsePart":
                # This is sent by the twilio_handler_realtime_humanlike.py handler
                pass
                
        except Exception as e:
            logger.error(f"Error handling stream mark: {str(e)}")
            
    def handle_stream_close(self):
        """Handle close events from Twilio Media Streams."""
        try:
            logger.info("Media Streams connection closed")
            
            # End the conversation if it's still active
            if self.is_active:
                self.end_conversation()
                
        except Exception as e:
            logger.error(f"Error handling stream close: {str(e)}")
    
    def handle_transcription(self, transcription):
        """
        Handle transcription from OpenAI Realtime.
        
        Args:
            transcription (str): The transcribed text
            
        Returns:
            str: The assistant's response
        """
        try:
            if not self.is_active:
                logger.warning("Conversation is not active")
                return None
                
            if not transcription:
                logger.warning("Empty transcription received")
                return None
                
            logger.info(f"User said: {transcription}")
            
            # Add to transcript
            self._add_to_transcript("user", transcription)
            
            # Add to LLM conversation
            self.llm.add_user_message(transcription)
            
            # Generate response
            response = self.llm.get_response()
            
            logger.info(f"Assistant response: {response}")
            
            # Add to transcript
            self._add_to_transcript("assistant", response)
            
            # Check if conversation goals are met
            self._check_conversation_goals()
            
            return response
            
        except Exception as e:
            logger.error(f"Error handling transcription: {str(e)}")
            return "I'm sorry, I'm having trouble processing your request."
            
    def _generate_greeting(self):
        """
        Generate an initial greeting.
        
        Returns:
            str: The greeting message
        """
        # Use the LLM to generate a greeting based on the prompt
        self.llm.add_user_message("The call has just started. Please introduce yourself according to the instructions.")
        greeting = self.llm.get_response()
        return greeting
        
    def _generate_farewell(self):
        """
        Generate a farewell message.
        
        Returns:
            str: The farewell message
        """
        # Use the LLM to generate a farewell
        self.llm.add_user_message("The call is ending now. Please say goodbye appropriately.")
        farewell = self.llm.get_response()
        return farewell
        
    def _add_to_transcript(self, speaker, text):
        """
        Add a message to the transcript.
        
        Args:
            speaker (str): The speaker ('user' or 'assistant')
            text (str): The message text
        """
        self.transcript.append({
            "timestamp": datetime.now().isoformat(),
            "speaker": speaker,
            "text": text
        })
        
    def _check_conversation_goals(self):
        """
        Check if conversation goals have been met.
        
        Returns:
            bool: True if goals are met, False otherwise
        """
        try:
            # Analyze the conversation
            analysis = self.llm.analyze_conversation()
            
            # Check if goals are completed
            if analysis.get('goals_completed', False) or analysis.get('can_end_call', False):
                logger.info(f"Conversation goals met: {analysis.get('reason', 'No reason provided')}")
                
                # End the conversation if it's still active
                if self.is_active:
                    self.end_conversation()
                    
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Error checking conversation goals: {str(e)}")
            return False
            
    def _save_conversation_data(self):
        """Save conversation data to storage."""
        try:
            # Calculate duration
            if self.start_time and self.end_time:
                duration_seconds = (self.end_time - self.start_time).total_seconds()
            else:
                duration_seconds = 0
                
            # Create metadata
            metadata = {
                "call_name": self.config.call_name,
                "phone_number": self.config.phone_number,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": self.end_time.isoformat() if self.end_time else None,
                "duration_seconds": duration_seconds,
                "prompt": self.config.prompt
            }
            
            # Save metadata
            self.storage.save_metadata(metadata)
            
            # Save transcript
            self.storage.save_transcript(self.transcript)
            
            # Save conversation history
            conversation_path = self.config.transcript_dir / f"{self.config.call_name}_conversation.json"
            self.llm.save_conversation(conversation_path)
            
            logger.info("Saved conversation data")
            
        except Exception as e:
            logger.error(f"Error saving conversation data: {str(e)}")