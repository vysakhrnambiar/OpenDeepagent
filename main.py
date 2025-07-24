# main.py

import sys
import os
import glob
from pathlib import Path
import asyncio
from typing import Optional

# --- The ONE AND ONLY Path Setup ---
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))
# ------------------------------------

# Import app_config and logger_setup first as they are foundational
from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger("MainApp", level_str=app_config.LOG_LEVEL)

# Import other components needed for services
from database.db_manager import initialize_database
from common.redis_client import RedisClient
from call_processor_service.asterisk_ami_client import AsteriskAmiClient
from call_processor_service.call_initiator_svc import CallInitiatorService
from task_manager.task_scheduler_svc import TaskSchedulerService
from audio_processing_service.audio_socket_server import AudioSocketServer # Added
from task_manager.orchestrator_svc import OrchestratorService  # Added for HITL
# --- Global Service Instances ---
# These will be initialized by start_background_services
redis_client: Optional[RedisClient] = None
ami_client: Optional[AsteriskAmiClient] = None
call_initiator_svc: Optional[CallInitiatorService] = None
task_scheduler_svc: Optional[TaskSchedulerService] = None
background_services_task: Optional[asyncio.Task] = None
# --- NEW GLOBAL INSTANCE ---
audio_socket_server: Optional[AudioSocketServer] = None # Added
orchestrator_svc: Optional[OrchestratorService] = None  # Added for HITL

# --- Lifecycle Functions (to be called by lifespan manager) ---
async def actual_start_services():
    """Initializes and starts all background services. Renamed to avoid conflict."""
    global redis_client, ami_client, call_initiator_svc, task_scheduler_svc, audio_socket_server, orchestrator_svc

    logger.info("actual_start_services: Initializing background services...")
    initialize_database() # Initialize DB here

    redis_client = RedisClient()
    try:
        await redis_client._get_async_redis_client()
        logger.info("actual_start_services: Redis client seems connected.")
    except Exception as e:
        logger.error(f"actual_start_services: Failed Redis: {e}")

    ami_client = AsteriskAmiClient()
    try:
        if app_config.ASTERISK_AMI_USER and app_config.ASTERISK_AMI_SECRET:
            if await ami_client.connect_and_login():
                logger.info("actual_start_services: Asterisk AMI client connected.")
            else:
                logger.error("actual_start_services: Asterisk AMI client failed initial connect.")
        else:
            logger.warning("actual_start_services: Asterisk AMI creds not set.")
    except Exception as e:
        logger.error(f"actual_start_services: Error AMI client: {e}")

    if ami_client and redis_client:
        call_initiator_svc = CallInitiatorService(ami_client=ami_client, redis_client=redis_client)
        logger.info("actual_start_services: CallInitiatorService initialized.")
    else:
        logger.error("actual_start_services: Could not init CallInitiatorService.")

    if call_initiator_svc:
        task_scheduler_svc = TaskSchedulerService(call_initiator_service=call_initiator_svc)
        logger.info("actual_start_services: TaskSchedulerService initialized.")
    else:
        logger.error("actual_start_services: Could not init TaskSchedulerService.")


    if redis_client: # AudioSocketServer needs RedisClient (passed to handler)
        audio_socket_server = AudioSocketServer(
            host=app_config.AUDIOSOCKET_HOST,
            port=app_config.AUDIOSOCKET_PORT,
            redis_client=redis_client
        )
        logger.info("actual_start_services: AudioSocketServer initialized.")
    else:
        logger.error("actual_start_services: Redis client not available, cannot initialize AudioSocketServer.")

    # --- Initialize OrchestratorService for HITL ---
    if redis_client:
        # Create a system-wide orchestrator for HITL handling
        # Using user_id=0 as a system user for global HITL handling
        orchestrator_svc = OrchestratorService(user_id=0, redis_client=redis_client)
        await orchestrator_svc.start_hitl_listener()
        logger.info("actual_start_services: OrchestratorService HITL listener started.")
    else:
        logger.error("actual_start_services: Redis client not available, cannot initialize OrchestratorService.")

    service_tasks_to_gather = []
    if task_scheduler_svc:
        service_tasks_to_gather.append(asyncio.create_task(task_scheduler_svc.run_scheduler_loop()))
        logger.info("actual_start_services: TaskSchedulerService loop started.")
    
    # --- ADD AUDIOSOCKETSERVER START TO TASKS ---
    if audio_socket_server:
        service_tasks_to_gather.append(asyncio.create_task(audio_socket_server.start()))
        logger.info("actual_start_services: AudioSocketServer start task created.")


    if service_tasks_to_gather:
        logger.info(f"actual_start_services: Running {len(service_tasks_to_gather)} bg tasks.")
        try:
            await asyncio.gather(*service_tasks_to_gather)
        except asyncio.CancelledError:
            logger.info("actual_start_services: Background services gathering task cancelled.")
        except Exception as e:
            logger.error(f"actual_start_services: Exception in gathered services: {e}", exc_info=True)
    else:
        logger.warning("actual_start_services: No background service tasks started.")

async def actual_shutdown_services():
    """Gracefully shuts down all background services. Renamed."""
    logger.info("actual_shutdown_services: Shutting down background services...")
    if task_scheduler_svc:
        task_scheduler_svc.stop_scheduler_loop()

    # --- STOP ORCHESTRATOR HITL LISTENER ---
    if orchestrator_svc:
        try:
            await orchestrator_svc.stop_hitl_listener()
            logger.info("actual_shutdown_services: OrchestratorService HITL listener stopped.")
        except Exception as e:
            logger.error(f"actual_shutdown_services: Error stopping OrchestratorService: {e}", exc_info=True)
    
    # --- STOP AUDIOSOCKETSERVER ---
    if audio_socket_server:
        try:
            await audio_socket_server.stop()
            logger.info("actual_shutdown_services: AudioSocketServer stopped.")
        except Exception as e:
            logger.error(f"actual_shutdown_services: Error stopping AudioSocketServer: {e}", exc_info=True)
            
    if ami_client:
        await ami_client.close()
    if redis_client:
        await redis_client.close_async_client()
    logger.info("actual_shutdown_services: Background services shutdown process initiated.")
    await asyncio.sleep(1) # Shorter sleep, gather in lifespan will wait for task.
    logger.info("actual_shutdown_services: Background services shutdown complete.")

# --- FastAPI App Import and Lifespan Integration ---
# This needs to come AFTER the functions it might use are defined,
# or app.py needs to import them.
# Let's assume web_interface.app will import these functions.

# The `fastapi_app` instance will be imported by uvicorn using "main:fastapi_app"
# So, we need to ensure it's defined here after everything it might depend on,
# or that web_interface.app.py correctly sets up its own lifespan
# that calls these main.py functions.

# For the cleanest approach, web_interface/app.py should define the lifespan
# and import what it needs.

# If __name__ == "__main__": block will be the primary entry point for Uvicorn
if __name__ == "__main__":
    # Log deletion is now handled automatically by the logger_setup module.
    # No need to call it here anymore.
    
    # This is where we tell uvicorn to run the app from web_interface.app
    # That app.py will have the lifespan manager.
    import uvicorn
    logger.info("==================================================")
    logger.info("         Starting OpenDeep Agent Platform         ")
    logger.info("==================================================")
    logger.info(f"Launching FastAPI server on http://{app_config.WEB_SERVER_HOST}:{app_config.WEB_SERVER_PORT}")

    uvicorn.run(
        "web_interface.app:app", # IMPORTANT: Point to app instance in web_interface.app
        host=app_config.WEB_SERVER_HOST,
        port=app_config.WEB_SERVER_PORT,
        reload=app_config.LOG_LEVEL == "DEBUG",
        log_level=app_config.LOG_LEVEL.lower()
    )