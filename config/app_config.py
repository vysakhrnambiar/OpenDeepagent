import os
from dotenv import load_dotenv
import logging

# Load environment variables from .env file
load_dotenv()

class AppConfig:
    # Database Configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./opendeep_app.db")

    # Redis Configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD") # Optional

    # OpenAI API Configuration
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    OPENAI_FORM_LLM_MODEL: str = os.getenv("OPENAI_FORM_LLM_MODEL", "gpt-4o") # For prompt generation, analysis
    OPENAI_REALTIME_LLM_MODEL: str = os.getenv("OPENAI_REALTIME_LLM_MODEL", "gpt-4o-realtime-preview-2024-10-01") # For live calls

    # Asterisk AMI Configuration
    ASTERISK_HOST: str = os.getenv("ASTERISK_HOST", "127.0.0.1")
    ASTERISK_PORT: int = int(os.getenv("ASTERISK_PORT", 5038))
    ASTERISK_AMI_USER: str | None = os.getenv("ASTERISK_AMI_USER")
    ASTERISK_AMI_SECRET: str | None = os.getenv("ASTERISK_AMI_SECRET")
    DEFAULT_ASTERISK_CONTEXT: str = os.getenv("DEFAULT_ASTERISK_CONTEXT", "default")
    DEFAULT_ASTERISK_CHANNEL_TYPE: str = os.getenv("DEFAULT_ASTERISK_CHANNEL_TYPE", "PJSIP") # or SIP, etc.
    DEFAULT_CALLER_ID_EXTEN: str = os.getenv("DEFAULT_CALLER_ID_EXTEN", "opendeep") # CallerID num part

    # AudioSocket Server Configuration (for Asterisk to connect to)
    AUDIOSOCKET_HOST: str = os.getenv("AUDIOSOCKET_HOST", "0.0.0.0") # Host for our audiosocket server
    AUDIOSOCKET_PORT: int = int(os.getenv("AUDIOSOCKET_PORT", 1200)) # Port for our audiosocket server

    # Application Settings
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    MAX_CONCURRENT_CALLS: int = int(os.getenv("MAX_CONCURRENT_CALLS", 10)) # For CallInitiatorService
    DEFAULT_MAX_TASK_ATTEMPTS: int = int(os.getenv("DEFAULT_MAX_TASK_ATTEMPTS", 3))
    TASK_SCHEDULER_POLL_INTERVAL_S: int = int(os.getenv("TASK_SCHEDULER_POLL_INTERVAL_S", 5)) # Seconds
    POST_CALL_ANALYZER_POLL_INTERVAL_S: int = int(os.getenv("POST_CALL_ANALYZER_POLL_INTERVAL_S", 10)) # Seconds

    # Web Interface Configuration
    WEB_SERVER_HOST: str = os.getenv("WEB_SERVER_HOST", "127.0.0.1")
    WEB_SERVER_PORT: int = int(os.getenv("WEB_SERVER_PORT", 8000))


    def __init__(self):
        # Basic validation for critical keys
        if not self.OPENAI_API_KEY:
            # Using print here as logger might not be set up when this class is imported/instantiated
            print("CRITICAL: OPENAI_API_KEY is not set in environment variables.", flush=True)
        if not self.ASTERISK_AMI_USER or not self.ASTERISK_AMI_SECRET:
            print("WARNING: ASTERISK_AMI_USER or ASTERISK_AMI_SECRET is not set. AMI operations will fail.", flush=True)

# Instantiate the config for easy import elsewhere
app_config = AppConfig()

if __name__ == "__main__":
    # Example of how to use it and test if values are loaded
    print(f"Database URL: {app_config.DATABASE_URL}")
    print(f"OpenAI Key Loaded: {'Yes' if app_config.OPENAI_API_KEY else 'No'}")
    print(f"Redis Host: {app_config.REDIS_HOST}")
    print(f"Asterisk Host: {app_config.ASTERISK_HOST}")
    print(f"Log Level: {app_config.LOG_LEVEL}")
    print(f"AudioSocket Host: {app_config.AUDIOSOCKET_HOST}:{app_config.AUDIOSOCKET_PORT}")