import sys
from pathlib import Path
import uvicorn

# --- The ONE AND ONLY Path Setup ---
# Add the project root to the Python path.
# This makes all modules discoverable.
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))
# ------------------------------------

from web_interface.app import app  # Now this import will work
from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger("MainApp")

if __name__ == "__main__":
    logger.info("==================================================")
    logger.info("         Starting OpenDeep Agent Platform         ")
    logger.info("==================================================")
    logger.info(f"Launching server on http://{app_config.WEB_SERVER_HOST}:{app_config.WEB_SERVER_PORT}")
    
    uvicorn.run(
        # Point to the app object in the web_interface.app module
        "web_interface.app:app", 
        host=app_config.WEB_SERVER_HOST,
        port=app_config.WEB_SERVER_PORT,
        reload=True,
        log_level="info"
    )