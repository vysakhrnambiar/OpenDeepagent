import sys
from pathlib import Path
import asyncio # Import asyncio
from typing import Optional 

# --- The ONE AND ONLY Path Setup ---
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))
# ------------------------------------

from web_interface.app import app as fastapi_app # Renamed to avoid conflict
from config.app_config import app_config
from common.logger_setup import setup_logger
from database.db_manager import initialize_database

# Import services
from common.redis_client import RedisClient
from call_processor_service.asterisk_ami_client import AsteriskAmiClient
from call_processor_service.call_initiator_svc import CallInitiatorService
from task_manager.task_scheduler_svc import TaskSchedulerService
# Import other services as they are developed (e.g., AudioSocketServer, PostCallAnalyzerService)

logger = setup_logger("MainApp", level_str=app_config.LOG_LEVEL)

# Global service instances (can be structured better with a context manager or dependency injection later)
redis_client: Optional[RedisClient] = None
ami_client: Optional[AsteriskAmiClient] = None
call_initiator_svc: Optional[CallInitiatorService] = None
task_scheduler_svc: Optional[TaskSchedulerService] = None
# audio_socket_server: Optional[AudioSocketServer] = None # For later

async def start_background_services():
    """Initializes and starts all background services."""
    global redis_client, ami_client, call_initiator_svc, task_scheduler_svc

    logger.info("Initializing background services...")

    # 1. Initialize Redis Client
    redis_client = RedisClient()
    # Test connection (optional, RedisClient constructor or first use might do this)
    try:
        # _get_async_redis_client attempts to connect and pings
        await redis_client._get_async_redis_client() 
        logger.info("Redis client seems connected.")
    except Exception as e:
        logger.error(f"Failed to ensure Redis client connection: {e}. Some services may not function.")
        # Depending on criticality, might exit or continue with degraded functionality

    # 2. Initialize Asterisk AMI Client
    ami_client = AsteriskAmiClient()
    try:
        if not (app_config.ASTERISK_AMI_USER and app_config.ASTERISK_AMI_SECRET):
            logger.warning("Asterisk AMI credentials not set. AMI client will not connect.")
        else:
            # connect_and_login will retry internally if it fails initially
            # It returns True on success, False if initial login attempt chain fails
            # The client will keep retrying in background if connect_and_login fails here but tasks are started
            connected = await ami_client.connect_and_login()
            if connected:
                logger.info("Asterisk AMI client connected and logged in.")
            else:
                logger.error("Asterisk AMI client failed initial connect_and_login. It will attempt to reconnect in the background.")
    except Exception as e:
        logger.error(f"Error during initial AMI client connection: {e}")


    # 3. Initialize Call Initiator Service (depends on AMI and Redis)
    if ami_client and redis_client: # Ensure dependencies are available
        call_initiator_svc = CallInitiatorService(ami_client=ami_client, redis_client=redis_client)
        logger.info("CallInitiatorService initialized.")
    else:
        logger.error("Could not initialize CallInitiatorService due to missing AMI or Redis client.")

    # 4. Initialize Task Scheduler Service (depends on CallInitiatorService)
    if call_initiator_svc:
        task_scheduler_svc = TaskSchedulerService(call_initiator_service=call_initiator_svc) # Pass dependency
        logger.info("TaskSchedulerService initialized.")
    else:
        logger.error("Could not initialize TaskSchedulerService due to missing CallInitiatorService.")

    # TODO: Initialize AudioSocketServer when ready
    # audio_socket_server = AudioSocketServer(...)
    # logger.info("AudioSocketServer initialized.")


    # Start main loops for services that have them
    service_tasks = []
    if task_scheduler_svc:
        service_tasks.append(asyncio.create_task(task_scheduler_svc.run_scheduler_loop()))
        logger.info("TaskSchedulerService loop started.")
    
    # AsteriskAmiClient's listener and keepalive tasks are started by its connect_and_login
    # CallInitiatorService does not have its own persistent loop; it's called by TaskScheduler.

    # TODO: Start AudioSocketServer's listening loop
    # if audio_socket_server:
    #     service_tasks.append(asyncio.create_task(audio_socket_server.start_server()))
    #     logger.info("AudioSocketServer started.")
    
    if service_tasks:
        logger.info(f"Running {len(service_tasks)} background service tasks.")
        try:
            await asyncio.gather(*service_tasks)
        except asyncio.CancelledError:
            logger.info("Background services parent task cancelled.")
        except Exception as e:
            logger.error(f"Exception in gathered background services: {e}", exc_info=True)
    else:
        logger.warning("No background service tasks were started.")


async def shutdown_background_services():
    """Gracefully shuts down all background services."""
    logger.info("Shutting down background services...")

    if task_scheduler_svc:
        task_scheduler_svc.stop_scheduler_loop() # Signal loop to stop
        # The task in asyncio.gather (in start_background_services) should then complete.
    
    # TODO: Stop AudioSocketServer
    # if audio_socket_server:
    #     await audio_socket_server.stop_server()

    if ami_client:
        await ami_client.close() # Closes connection and cancels its internal tasks
    
    if redis_client:
        await redis_client.close_async_client() # Closes async Redis client

    logger.info("Background services shutdown process initiated.")
    # Allow some time for tasks to clean up if asyncio.gather isn't sufficient
    await asyncio.sleep(2) 
    logger.info("Background services shutdown complete.")


# --- Main Application Lifecycle ---
background_tasks_handle: Optional[asyncio.Task] = None

@fastapi_app.on_event("startup")
async def startup_event():
    global background_tasks_handle
    logger.info("Application startup: Initializing database and starting background services...")
    initialize_database()
    # Run background services in a separate task so FastAPI startup isn't blocked indefinitely
    background_tasks_handle = asyncio.create_task(start_background_services())
    logger.info("Background services task created.")


@fastapi_app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown: Stopping background services...")
    if background_tasks_handle and not background_tasks_handle.done():
        background_tasks_handle.cancel() # Cancel the main background services gathering task
        try:
            await background_tasks_handle # Wait for it to actually cancel
        except asyncio.CancelledError:
            logger.info("Main background services task successfully cancelled.")
    
    # Explicitly call shutdown for individual services to ensure order if needed
    # or rely on cancellation above to propagate.
    # The `shutdown_background_services` function will be called if the gather task exits.
    # For more direct control during FastAPI shutdown, we can call it here too,
    # but it might lead to double calls if not handled carefully.
    # For now, relying on the cancellation of `background_tasks_handle` to trigger its finally block
    # or for `start_background_services` to complete.
    # A more robust shutdown might involve custom signals or events for each service.
    
    await shutdown_background_services() # Call explicit shutdown
    logger.info("Application shutdown complete.")


if __name__ == "__main__":
    import uvicorn
    logger.info("==================================================")
    logger.info("         Starting OpenDeep Agent Platform         ")
    logger.info("==================================================")
    logger.info(f"Launching FastAPI server on http://{app_config.WEB_SERVER_HOST}:{app_config.WEB_SERVER_PORT}")
    
    # Uvicorn will run the FastAPI app, which triggers startup/shutdown events
    uvicorn.run(
        "main:fastapi_app", # Points to the FastAPI app instance in this file
        host=app_config.WEB_SERVER_HOST,
        port=app_config.WEB_SERVER_PORT,
        reload=app_config.LOG_LEVEL == "DEBUG", # Enable reload only in DEBUG for convenience
        log_level=app_config.LOG_LEVEL.lower()
    )
    # Note: When uvicorn exits (e.g. Ctrl+C), the shutdown_event should be called.