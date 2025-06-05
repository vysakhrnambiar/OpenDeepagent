import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Assuming app_config is accessible or we pass log_level and log_dir directly
# For simplicity here, let's assume we can import app_config
# If that causes circular import issues later, we'll pass parameters.
try:
    from config.app_config import app_config
    DEFAULT_LOG_LEVEL = app_config.LOG_LEVEL
    # Define a base directory for logs, perhaps relative to this file or a configured path
    # For now, let's assume logs go into a 'logs' subdirectory of the project root
    PROJECT_ROOT = Path(__file__).resolve().parent.parent # Goes up two levels from common/
    LOG_DIR = PROJECT_ROOT / "logs"

except ImportError:
    # Fallback if app_config can't be imported (e.g., during early init or testing this file standalone)
    print("Warning: app_config not found for logger_setup. Using default log settings.", flush=True)
    DEFAULT_LOG_LEVEL = "INFO"
    PROJECT_ROOT = Path(".") # Current directory
    LOG_DIR = PROJECT_ROOT / "logs"


# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Custom StreamHandler that handles encoding errors gracefully (from your original asty.py)
class EncodingStreamHandler(logging.StreamHandler):
    def __init__(self, stream=None, encoding='utf-8'):
        super().__init__(stream)
        # self.encoding = encoding # Not directly used in this simplified version from asty
        # Python's default sys.stdout/stderr should handle encoding if environment is set up.
        # If specific encoding issues arise, this class can be expanded.

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            # Fallback for encoding errors
            try:
                stream = self.stream
                # Attempt to encode with replace, then decode back for writing
                # This is a bit of a workaround; ideally, the environment handles UTF-8
                stream.write(msg.encode(sys.getdefaultencoding(), 'replace').decode(sys.getdefaultencoding()) + self.terminator)
                self.flush()
            except Exception:
                self.handleError(record) # Default error handling
        except Exception:
            self.handleError(record)


def setup_logger(name="OpenDeepApp", level_str=None, log_to_file=True, log_to_console=True):
    """
    Set up a logger instance.
    """
    if level_str is None:
        level_str = DEFAULT_LOG_LEVEL

    numeric_level = getattr(logging, level_str.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(numeric_level)
    logger.propagate = False # Prevents double logging if root logger is also configured

    # Clear existing handlers to avoid duplicates if called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - [%(threadName)s] - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    )

    if log_to_console:
        console_handler = EncodingStreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(numeric_level) # Set level for handler too
        logger.addHandler(console_handler)

    if log_to_file:
        log_file_path = LOG_DIR / f"{name.lower().replace(' ', '_')}.log"
        # Use RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(numeric_level) # Set level for handler too
        logger.addHandler(file_handler)
        # print(f"Logging to file: {log_file_path}", flush=True) # For initial debug

    return logger

# Example of creating a default logger instance that can be imported
# You might create specific loggers for different modules too.
# e.g., main_logger = setup_logger("MainApp")
#       web_logger = setup_logger("WebApp")

if __name__ == "__main__":
    # Test the logger setup
    # This requires config.app_config to be importable, or it uses defaults
    logger1 = setup_logger("TestLogger1", level_str="DEBUG")
    logger1.debug("This is a debug message from TestLogger1.")
    logger1.info("This is an info message from TestLogger1.")

    logger2 = setup_logger("TestLogger2", level_str="WARNING", log_to_file=False)
    logger2.warning("This is a warning from TestLogger2 (console only).")

    # To demonstrate the log file creation
    if os.path.exists(LOG_DIR / "testlogger1.log"):
        print(f"Log file created at {LOG_DIR / 'testlogger1.log'}", flush=True)
    if not os.path.exists(LOG_DIR / "testlogger2.log"):
        print(f"Log file for TestLogger2 was not created, as expected.", flush=True)