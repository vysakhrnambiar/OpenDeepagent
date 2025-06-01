#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
OpenDeep - Outbound Caller Application

This script is the entry point for the OpenDeep application, which combines
Twilio for phone calls, OpenAI Realtime for conversation intelligence, and
Deepgram for text-to-speech.
"""

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import signal
import socket
from pathlib import Path
from dotenv import load_dotenv

# Import application modules
from config import Config
from logger import setup_logger
from llm import LLMHandler
from storage import StorageManager
from conversation import ConversationManager
from twilio_handler_realtime_experimental_v2 import TwilioHandler


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='OpenDeep Application')
    parser.add_argument('--config', type=str, default='config/call_configs/default.json',
                        help='Path to the call configuration JSON file')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('--output-dir', type=str,
                        help='Custom output directory for call data')
    parser.add_argument('--stream-url', type=str,
                        help='URL for Twilio Media Streams (required for production)')
    parser.add_argument('--port', type=int, default=8080,
                        help='Port for Media Streams server (default: 8080)')
    return parser.parse_args()


def validate_config_file(config_path):
    """Validate that the config file exists and is valid JSON."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # Check for required fields
        if 'phone_number' not in config_data:
            print("Error: Config file missing required field 'phone_number'")
            sys.exit(1)
        if 'prompt' not in config_data:
            print("Error: Config file missing required field 'prompt'")
            sys.exit(1)
            
        return config_data
    except json.JSONDecodeError:
        print(f"Error: Config file is not valid JSON: {config_path}")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading config file: {str(e)}")
        sys.exit(1)


def setup_signal_handlers(conversation_manager, twilio_handler):
    """Set up signal handlers for graceful shutdown."""
    def signal_handler(sig, frame):
        print("\nReceived interrupt signal. Shutting down gracefully...")
        if conversation_manager and conversation_manager.is_active:
            conversation_manager.end_conversation()
        if twilio_handler:
            if twilio_handler.call:
                twilio_handler.end_call()
            twilio_handler.stop_stream_server()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def get_public_ip():
    """Get the public IP address of the machine."""
    try:
        # This is a simple way to get the public IP, but it requires internet access
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"  # Fallback to localhost


def main():
    """Main entry point for the application."""
    # Load environment variables
    load_dotenv()
    
    # Parse command-line arguments
    args = parse_arguments()
    
    # Validate the config file
    config_data = validate_config_file(args.config)
    
    # Set up logging
    log_level = 'DEBUG'  # Force DEBUG level for detailed logs
    logger = setup_logger(log_level)
    logger.info("Starting OpenDeep Application")
    
    # Initialize configuration
    output_dir = args.output_dir if args.output_dir else None
    config = Config(config_data, output_dir)
    logger.info(f"Loaded configuration for call to {config.phone_number}")
    
    try:
        # Initialize components
        logger.info("Initializing components")
        
        # Initialize storage manager
        storage_manager = StorageManager(config)
        
        # Initialize LLM handler
        llm_handler = LLMHandler(config)
        
        # Initialize Twilio handler with Deepgram TTS
        twilio_handler = TwilioHandler(config)
        
        # Set system prompt explicitly for experimental handler
        if hasattr(twilio_handler, 'system_prompt'):
            logger.info("Setting system prompt for experimental handler")
            twilio_handler.system_prompt = config.get_system_prompt()
        
        # Start API thread for experimental handler if it has run_api_loop method
        if hasattr(twilio_handler, 'run_api_loop') and hasattr(twilio_handler, 'api_ready_event'):
            logger.info("Starting API thread for experimental handler")
            api_thread = threading.Thread(target=twilio_handler.run_api_loop, daemon=True)
            api_thread.start()
            
            # Wait for API to be ready
            logger.info("Waiting for API to be ready...")
            twilio_handler.api_ready_event.wait(timeout=10)
            if twilio_handler.api_ready_event.is_set():
                logger.info("API is ready")
            else:
                logger.warning("API ready event not set within timeout")
        
        # Initialize conversation manager
        conversation_manager = ConversationManager(
            config, llm_handler, storage_manager, twilio_handler
        )
        
        # Set up signal handlers for graceful shutdown
        setup_signal_handlers(conversation_manager, twilio_handler)
        
        # Start the conversation to generate greeting
        greeting_text = conversation_manager.start_conversation()
        if greeting_text:
            logger.info("Generated greeting successfully")
            
            # Set the initial greeting text for Deepgram TTS to use
            twilio_handler.initial_greeting_text = greeting_text
            logger.info(f"Set initial greeting text for Deepgram TTS: '{greeting_text}'")
            
            # Add test message handler for older handler versions
            def test_message_handler(text_input):
                # Always respond with a test message to verify TTS functionality
                logger.info(f"TEST HANDLER: Received: '{text_input}'")
                logger.info(f"TEST HANDLER: Returning fixed test response")
                return "Hello! This is a test response from the AI assistant. Can you hear me clearly? If you can hear this, please say something."
            
            # Use the test handler instead of the regular one for debugging (only if the handler supports it)
            if hasattr(twilio_handler, 'set_transcription_callback'):
                logger.info("Setting test message handler")
                twilio_handler.set_transcription_callback(test_message_handler)
            else:
                logger.info("Experimental handler detected - direct OpenAI conversation management will be used")
            
            # Set transcription callback for conversation manager
            def handle_transcription(transcription):
                logger.info(f"Received transcription: {transcription}")
                
                # Use the conversation manager to handle transcription
                ai_response = conversation_manager.handle_transcription(transcription)
                logger.info(f"AI response: {ai_response}")
                
                return ai_response
            
            # Set the transcription callback if the handler supports it
            if hasattr(twilio_handler, 'set_transcription_callback'):
                twilio_handler.set_transcription_callback(handle_transcription)
            
            # Check if stream URL is provided
            stream_url = args.stream_url
            
            # If no stream URL provided, try to create a local server
            if not stream_url:
                # Get the local IP address
                ip_address = get_public_ip()
                port = args.port
                
                # Create a stream URL
                # Note: This won't work in production as Twilio needs a public URL
                stream_url = f"wss://{ip_address}:{port}"
                
                logger.warning(f"No stream URL provided. Using local URL: {stream_url}")
                logger.warning("This will only work if Twilio can reach this URL.")
                logger.warning("For production, use a public URL with --stream-url.")
            
            # Make the call with Media Streams
            # Check if the method is async (coroutine) and handle accordingly
            if asyncio.iscoroutinefunction(twilio_handler.make_call_with_media_streams):
                logger.info("Using async call method")
                # Use asyncio to run the coroutine
                call = asyncio.run(twilio_handler.make_call_with_media_streams(stream_url))
            else:
                logger.info("Using sync call method")
                call = twilio_handler.make_call_with_media_streams(stream_url)
            
            if call:
                logger.info(f"Call initiated with SID: {call.sid}")
                
                # Wait for the call to complete
                logger.info("Call in progress. Press Ctrl+C to end.")
            
            # Keep the application running until interrupted
            try:
                while True:
                    # Check call status periodically
                    status = twilio_handler.get_call_status()
                    if status in ["completed", "failed", "busy", "no-answer", "canceled"]:
                        logger.info(f"Call ended with status: {status}")
                        break
                    
                    time.sleep(5)
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                
            # End the conversation if still active
            if conversation_manager.is_active:
                conversation_manager.end_conversation()
        
        logger.info("OpenDeep Application completed successfully")
        return 0
        
    except Exception as e:
        logger.error(f"Error in OpenDeep Application: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())