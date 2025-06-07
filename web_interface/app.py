# web_interface/app.py

import sys
from pathlib import Path
import uvicorn # Import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from web_interface import routes_ui, routes_api
from database.db_manager import initialize_database
from llm_integrations.openai_form_client import OpenAIFormClient
from llm_integrations.google_gemini_client import GoogleGeminiClient
from tools.information_retriever_svc import InformationRetrieverService
from task_manager.ui_assistant_svc import UIAssistantService
from common.logger_setup import setup_logger
from config.app_config import app_config # To get host and port

logger = setup_logger("WebApp", level_str=app_config.LOG_LEVEL)

# --- Service Initialization ---
# This section remains the same, creating our single instances of services.
try:
    initialize_database()
    openai_client = OpenAIFormClient()
    gemini_client = GoogleGeminiClient()
    retriever_service = InformationRetrieverService(gemini_client=gemini_client)
    ui_assistant_service = UIAssistantService(
        openai_client=openai_client,
        info_retriever=retriever_service
    )
except Exception as e:
    logger.critical(f"CRITICAL: Failed to initialize services on startup: {e}", exc_info=True)
    sys.exit(1)

# --- Dependency Injection Function ---
def get_ui_assistant_service() -> UIAssistantService:
    return ui_assistant_service

# --- FastAPI App Creation ---
def create_app() -> FastAPI:
    app = FastAPI()

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Include UI routes (serves index.html)
    app.include_router(routes_ui.router)

    # Include API routes and inject the real service, breaking the circular import
    app.dependency_overrides[routes_api.get_assistant_dependency] = get_ui_assistant_service
    app.include_router(routes_api.router, prefix="/api")

    logger.info("FastAPI app created and configured.")
    return app

# Create the app instance
app = create_app()

# THIS BLOCK WAS MISSING. IT IS NOW RESTORED.
# This is what makes the server run continuously.
if __name__ == "__main__":
    logger.info(f"Starting Uvicorn server on {app_config.WEB_SERVER_HOST}:{app_config.WEB_SERVER_PORT}")
    uvicorn.run(
        "web_interface.app:app",
        host=app_config.WEB_SERVER_HOST,
        port=app_config.WEB_SERVER_PORT,
        reload=True
    )