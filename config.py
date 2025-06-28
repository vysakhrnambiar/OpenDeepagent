# ===============================================================================
# LEGACY FILE - NOT CURRENTLY USED IN ACTIVE SYSTEM (v16.0+)
# 
# Status: PRESERVED for reference/potential future use
# Last Active: Early development phases (v1-v12)
# Replacement: config/app_config.py
# Safe to ignore: This file is not imported by main.py or active services
# 
# Historical Context: Original configuration system before modular architecture.
#                    Used for single-file implementations with direct config file
#                    loading. Replaced by centralized app_config.py with environment
#                    variable management.
# ===============================================================================
#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Configuration Management Module

This module handles loading and managing configuration for the OpenDeep application.
It includes functionality for parsing call configuration files and managing API keys.
"""

import os
import json
from pathlib import Path
from datetime import datetime
import logging

# Get the logger
logger = logging.getLogger(__name__)


class Config:
    """Configuration manager for the OpenDeep application."""
    
    def __init__(self, config_data, custom_output_dir=None):
        """
        Initialize the configuration manager.
        
        Args:
            config_data (dict): The configuration data from the call config file
            custom_output_dir (str, optional): Custom output directory for call data
        """
        self.config_data = config_data
        self._setup_paths(custom_output_dir)
        self._load_api_keys()
        self._setup_call_params()
        
    def _setup_paths(self, custom_output_dir):
        """Set up paths for data storage."""
        # Base directory is the parent of the current file's directory
        self.base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        
        # Set up data directories
        if custom_output_dir:
            self.data_dir = Path(custom_output_dir)
        else:
            self.data_dir = self.base_dir / 'data'
        
        # Create timestamp for this call
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Get call name from config or use timestamp
        call_name = self.config_data.get('call_name', f'call_{timestamp}')
        
        # Set up specific directories for this call
        self.call_dir = self.data_dir / 'calls' / call_name
        self.transcript_dir = self.data_dir / 'transcripts' / call_name
        self.log_dir = self.data_dir / 'logs'
        
        # Create directories if they don't exist
        os.makedirs(self.call_dir, exist_ok=True)
        os.makedirs(self.transcript_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        
        # Set up file paths
        self.recording_path = self.call_dir / f'{call_name}.wav'
        self.transcript_path = self.transcript_dir / f'{call_name}.json'
        self.metadata_path = self.transcript_dir / f'{call_name}_metadata.json'
        
        logger.debug(f"Set up paths for call: {call_name}")
        
    def _load_api_keys(self):
        """Load API keys from environment variables."""
        # Twilio credentials
        self.twilio_account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.twilio_auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.twilio_phone_number = os.environ.get('TWILIO_PHONE_NUMBER')
        
        # OpenAI credentials
        self.openai_api_key = os.environ.get('OPENAI_API_KEY')
        
        # Deepgram credentials
        self.deepgram_api_key = os.environ.get('DEEPGRAM_API_KEY')
        
        # Validate required credentials
        if not self.twilio_account_sid or not self.twilio_auth_token or not self.twilio_phone_number:
            logger.error("Missing Twilio credentials. Please set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER environment variables.")
            raise ValueError("Missing Twilio credentials")
            
        if not self.openai_api_key:
            logger.error("Missing OpenAI API key. Please set OPENAI_API_KEY environment variable.")
            raise ValueError("Missing OpenAI API key")
            
        if not self.deepgram_api_key:
            logger.error("Missing Deepgram API key. Please set DEEPGRAM_API_KEY environment variable.")
            raise ValueError("Missing Deepgram API key")
            
        logger.debug("Loaded API keys from environment variables")
        
    def _setup_call_params(self):
        """Set up call parameters from the config data."""
        # Required parameters
        self.phone_number = self.config_data['phone_number']
        self.prompt = self.config_data['prompt']
        
        # Optional parameters with defaults
        self.call_name = self.config_data.get('call_name', f'call_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        self.max_duration = self.config_data.get('max_duration', 300)  # Default 5 minutes
        self.voice = self.config_data.get('voice', 'alloy')  # Default voice for TTS
        self.deepgram_model = self.config_data.get('deepgram_model', 'aura-2-thalia-en')  # Default Deepgram voice model
        self.verbose_logging = self.config_data.get('verbose_logging', False)
        self.save_recording = self.config_data.get('save_recording', True)
        self.save_transcript = self.config_data.get('save_transcript', True)
        
        logger.debug(f"Set up call parameters for {self.phone_number}")
        
    def save_metadata(self, metadata):
        """
        Save metadata about the call.
        
        Args:
            metadata (dict): Metadata to save
        """
        with open(self.metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        logger.debug(f"Saved metadata to {self.metadata_path}")
        
    def get_system_prompt(self):
        """
        Get the system prompt for the LLM.
        
        Returns:
            str: The system prompt
        """
        # Basic system prompt template
        system_prompt = (
            "You are an AI assistant making a phone call. "
            "Your goal is to have a natural conversation based on the following instructions:\n\n"
            f"{self.prompt}\n\n"
            "Remember to be polite, speak naturally, and listen carefully to the person's responses. "
            "End the call appropriately when your goal is achieved or if the person wants to end the conversation."
        )
        return system_prompt