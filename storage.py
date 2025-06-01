#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Storage Module

This module handles storage management for call recordings, transcripts, and metadata.
"""

import logging
import os
import json
import shutil
from pathlib import Path
from datetime import datetime
import requests

# Get the logger
logger = logging.getLogger(__name__)


class StorageManager:
    """Handler for storage management."""
    
    def __init__(self, config):
        """
        Initialize the storage manager.
        
        Args:
            config (Config): The application configuration
        """
        self.config = config
        self._ensure_directories()
        logger.debug("Storage manager initialized")
        
    def _ensure_directories(self):
        """Ensure that all required directories exist."""
        try:
            # Create directories if they don't exist
            os.makedirs(self.config.call_dir, exist_ok=True)
            os.makedirs(self.config.transcript_dir, exist_ok=True)
            os.makedirs(self.config.log_dir, exist_ok=True)
            
            logger.debug("Ensured all storage directories exist")
        except Exception as e:
            logger.error(f"Error ensuring directories: {str(e)}")
            raise
            
    def save_recording(self, audio_data, file_path=None):
        """
        Save a call recording.
        
        Args:
            audio_data (bytes): The audio data to save
            file_path (str, optional): Custom file path to save to
            
        Returns:
            str: The path where the recording was saved
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.recording_path
                
            # Save the audio data
            with open(file_path, 'wb') as f:
                f.write(audio_data)
                
            logger.info(f"Saved recording to {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving recording: {str(e)}")
            return None
            
    def save_recording_from_url(self, url, file_path=None):
        """
        Download and save a recording from a URL.
        
        Args:
            url (str): The URL to download from
            file_path (str, optional): Custom file path to save to
            
        Returns:
            str: The path where the recording was saved
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.recording_path
                
            # Download the recording
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            # Save the recording
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            logger.info(f"Saved recording from URL to {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving recording from URL: {str(e)}")
            return None
            
    def save_transcript(self, transcript_data, file_path=None):
        """
        Save a call transcript.
        
        Args:
            transcript_data (dict): The transcript data to save
            file_path (str, optional): Custom file path to save to
            
        Returns:
            str: The path where the transcript was saved
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.transcript_path
                
            # Save the transcript data
            with open(file_path, 'w') as f:
                json.dump(transcript_data, f, indent=2)
                
            logger.info(f"Saved transcript to {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving transcript: {str(e)}")
            return None
            
    def save_metadata(self, metadata, file_path=None):
        """
        Save call metadata.
        
        Args:
            metadata (dict): The metadata to save
            file_path (str, optional): Custom file path to save to
            
        Returns:
            str: The path where the metadata was saved
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.metadata_path
                
            # Add timestamp to metadata
            metadata['timestamp'] = datetime.now().isoformat()
            
            # Save the metadata
            with open(file_path, 'w') as f:
                json.dump(metadata, f, indent=2)
                
            logger.info(f"Saved metadata to {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving metadata: {str(e)}")
            return None
            
    def load_recording(self, file_path=None):
        """
        Load a call recording.
        
        Args:
            file_path (str, optional): Custom file path to load from
            
        Returns:
            bytes: The audio data
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.recording_path
                
            # Check if the file exists
            if not os.path.exists(file_path):
                logger.error(f"Recording file not found: {file_path}")
                return None
                
            # Load the audio data
            with open(file_path, 'rb') as f:
                audio_data = f.read()
                
            logger.debug(f"Loaded recording from {file_path}")
            return audio_data
            
        except Exception as e:
            logger.error(f"Error loading recording: {str(e)}")
            return None
            
    def load_transcript(self, file_path=None):
        """
        Load a call transcript.
        
        Args:
            file_path (str, optional): Custom file path to load from
            
        Returns:
            dict: The transcript data
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.transcript_path
                
            # Check if the file exists
            if not os.path.exists(file_path):
                logger.error(f"Transcript file not found: {file_path}")
                return None
                
            # Load the transcript data
            with open(file_path, 'r') as f:
                transcript_data = json.load(f)
                
            logger.debug(f"Loaded transcript from {file_path}")
            return transcript_data
            
        except Exception as e:
            logger.error(f"Error loading transcript: {str(e)}")
            return None
            
    def load_metadata(self, file_path=None):
        """
        Load call metadata.
        
        Args:
            file_path (str, optional): Custom file path to load from
            
        Returns:
            dict: The metadata
        """
        try:
            # Use the default path if none provided
            if file_path is None:
                file_path = self.config.metadata_path
                
            # Check if the file exists
            if not os.path.exists(file_path):
                logger.error(f"Metadata file not found: {file_path}")
                return None
                
            # Load the metadata
            with open(file_path, 'r') as f:
                metadata = json.load(f)
                
            logger.debug(f"Loaded metadata from {file_path}")
            return metadata
            
        except Exception as e:
            logger.error(f"Error loading metadata: {str(e)}")
            return None