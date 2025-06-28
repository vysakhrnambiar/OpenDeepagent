# ===============================================================================
# LEGACY FILE - NOT CURRENTLY USED IN ACTIVE SYSTEM (v16.0+)
# 
# Status: PRESERVED for reference/potential future use
# Last Active: Early development phases (v1-v12)
# Replacement: common/logger_setup.py
# Safe to ignore: This file is not imported by main.py or active services
# 
# Historical Context: Original logging setup before modular architecture.
#                    Used custom encoding handler for UTF-8 support. Replaced by
#                    centralized logger_setup.py with improved configuration management.
# ===============================================================================
#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Logging Module

This module sets up logging for the OpenDeep application.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import io

# Custom StreamHandler that handles encoding errors gracefully
class EncodingStreamHandler(logging.StreamHandler):
    def __init__(self, stream=None, encoding='utf-8'):
        super().__init__(stream)
        self.encoding = encoding
        
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            # Replace problematic characters if they can't be encoded
            stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            # If encoding fails, replace problematic characters
            msg = self.format(record)
            try:
                stream = self.stream
                stream.write(msg.encode(self.encoding, errors='replace').decode(self.encoding) + self.terminator)
                self.flush()
            except Exception:
                self.handleError(record)
        except Exception:
            self.handleError(record)


def setup_logger(level='INFO'):
    """
    Set up the logger for the application.
    
    Args:
        level (str): The logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        
    Returns:
        logging.Logger: The configured logger
    """
    # Convert string level to logging level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f'Invalid log level: {level}')
    
    # Create logger
  

    logger = logging.getLogger('OpenDeep')
    logger.setLevel(numeric_level)
    
    # Remove existing handlers if any
    if logger.handlers:
        for handler in logger.handlers[:]: # Iterate over a copy
            logger.removeHandler(handler)
            handler.close() # Important to close handlers before removing

    
    # Create console handler with UTF-8 encoding
    console_handler = EncodingStreamHandler(sys.stdout, encoding='utf-8')
    console_handler.setLevel(numeric_level)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    
    # Add console handler to logger
    logger.addHandler(console_handler)
    
    # Create file handler
    try:
        # Base directory is the parent of the current file's directory
        base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        log_dir = base_dir / 'data' / 'logs'
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = log_dir / 'OpenDeep.log'
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        logger.debug(f"Logging to file: {log_file}")
    except Exception as e:
        logger.warning(f"Could not set up file logging: {str(e)}")
    
    logger.info(f"Logger initialized with level {level}")
    return logger


# Example usage
if __name__ == "__main__":
    logger = setup_logger('DEBUG')
    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.critical("This is a critical message")