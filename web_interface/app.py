import sys
from pathlib import Path
import uvicorn

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from web_interface import routes_ui, routes_api
from database import db_manager
from config.app_config import app_config

# Import the services that the API routes will depend on
from llm_integrations.openai_form_client import OpenAIFormClient
from task_manager.ui_assistant_svc import UIAssistantService

def create_app() -> FastAPI:
    """Creates and configures the main FastAPI application instance."""
    
    # Initialize the database (creates tables if they don't exist)
    db_manager.initialize_database()

    # Create singleton instances of our services
    # These will be shared across all API requests, which is efficient.
    try:
        openai_client = OpenAIFormClient()
        ui_assistant_service = UIAssistantService(openai_form_client=openai_client)
        # In the future, the orchestrator service would also be created here.
    except Exception as e:
        print(f"CRITICAL: Failed to initialize services on startup: {e}")
        # This will prevent the app from starting if essential config is missing, which is good.
        raise

    # Create the FastAPI app
    app = FastAPI(
        title="OpenDeep AI Calling Platform",
        description="A platform to schedule, manage, and analyze AI-powered outbound calls.",
        version="1.0.0"
    )

    # Make services available to the app state so routes can access them
    # This is a simple way to handle dependencies.
    app.state.ui_assistant_service = ui_assistant_service
    # app.state.orchestrator_service = orchestrator_service # For the future

    # Mount the static files directory
    static_path = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Include the API and UI routers
    app.include_router(routes_api.router)
    app.include_router(routes_ui.router)
    
    # Add a root redirect to the main UI page for convenience
    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/ui/")

    return app

# --- Main application instance ---
app = create_app()

# --- Direct execution for testing ---
if __name__ == "__main__":
    print("Starting Uvicorn server for the OpenDeep web interface...")
    print(f"Access the UI at http://{app_config.WEB_SERVER_HOST}:{app_config.WEB_SERVER_PORT}")
    uvicorn.run(
        "web_interface.app:app",
        host=app_config.WEB_SERVER_HOST,
        port=app_config.WEB_SERVER_PORT,
        reload=True,
        log_level="info"
    )