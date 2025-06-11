# web_interface/app.py

import asyncio # Add asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from contextlib import asynccontextmanager # Add asynccontextmanager
from typing import AsyncGenerator, Optional          # Add AsyncGenerator and Optional

# --- Path Setup for main module access ---
# This ensures that when app.py is loaded, main.py's directory (project root) is in path
# allowing imports like 'from main import actual_start_services'
import sys
_project_root_from_app = Path(__file__).resolve().parent.parent # Goes up two levels from web_interface/app.py to project root
if str(_project_root_from_app) not in sys.path:
    sys.path.insert(0, str(_project_root_from_app))
# --- End Path Setup ---

# Import the lifecycle functions from main.py
# These functions (actual_start_services, actual_shutdown_services, initialize_database)
# and the logger are expected to be defined in main.py at the project root.
try:
    from main import actual_start_services, actual_shutdown_services, initialize_database
    from main import logger as main_logger # Use the logger setup in main.py
except ImportError as e:
    # Fallback or critical error if main.py or its contents cannot be imported
    # This might happen if the structure is different or if there's a circular dependency issue at runtime.
    # For now, we'll proceed assuming the import works. A more robust solution might involve a shared services module.
    print(f"CRITICAL ERROR: Could not import lifecycle functions or logger from main.py: {e}")
    # Define dummy functions and logger to allow FastAPI to at least try to start for further debugging
    async def actual_start_services(): print("Dummy actual_start_services called")
    async def actual_shutdown_services(): print("Dummy actual_shutdown_services called")
    def initialize_database(): print("Dummy initialize_database called")
    import logging
    main_logger = logging.getLogger("WebApp_Fallback")
    main_logger.warning("Using fallback logger and lifecycle functions due to import error from main.py.")


# Import API and UI routers from the current package (web_interface)
from . import routes_ui, routes_api # Relative imports for local package

# Store the background task handle globally within the scope of app.py for the lifespan manager
_lifespan_background_task: Optional[asyncio.Task] = None

@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
    global _lifespan_background_task
    main_logger.info("Lifespan: Application startup sequence initiated...")
    
    try:
        initialize_database() # Call DB init
        main_logger.info("Lifespan: Database initialization complete.")
    except Exception as e_db:
        main_logger.error(f"Lifespan: Error during database initialization: {e_db}", exc_info=True)
        # Decide if to raise or continue if DB is not critical for *all* app functions initially
    
    # Create a task to run the service initialization and their main loops
    try:
        _lifespan_background_task = asyncio.create_task(actual_start_services())
        main_logger.info("Lifespan: Background services startup task created.")
    except Exception as e_bg_start:
        main_logger.error(f"Lifespan: Error creating background services task: {e_bg_start}", exc_info=True)

    try:
        yield # Application runs here
    finally:
        main_logger.info("Lifespan: Application shutdown sequence initiated...")
        if _lifespan_background_task and not _lifespan_background_task.done():
            main_logger.info("Lifespan: Cancelling background services task...")
            _lifespan_background_task.cancel()
            try:
                await _lifespan_background_task
            except asyncio.CancelledError:
                main_logger.info("Lifespan: Background services task successfully cancelled.")
            except Exception as e_await_cancel: # Catch other exceptions during await
                main_logger.error(f"Lifespan: Exception while awaiting cancelled background_services_task: {e_await_cancel}", exc_info=True)
        
        try:
            await actual_shutdown_services() # Call shutdown for services
            main_logger.info("Lifespan: Background services shutdown procedures complete.")
        except Exception as e_bg_shutdown:
            main_logger.error(f"Lifespan: Error during actual_shutdown_services: {e_bg_shutdown}", exc_info=True)
        
        main_logger.info("Lifespan: Application shutdown sequence finished.")

# Create the FastAPI application instance
app = FastAPI(
    title="OpenDeep Agent Platform",
    description="An AI-powered outbound calling and task management system.",
    version="1.0.0",
    lifespan=lifespan # Assign the lifespan manager here
)

# Include routers for API and UI
app.include_router(routes_api.router, prefix="/api", tags=["API"])
app.include_router(routes_ui.router, tags=["UI"]) # Or prefix="/ui" if all UI routes are under /ui

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.is_dir(): # More robust check
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    main_logger.warning(f"Static directory not found at {static_dir}, /static route not mounted.")