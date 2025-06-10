# task_manager/task_scheduler_svc.py

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import List

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from database import db_manager
from database.models import Task, TaskStatus
from common.logger_setup import setup_logger
from call_processor_service.call_initiator_svc import CallInitiatorService # Import CallInitiatorService

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class TaskSchedulerService:
    def __init__(self, call_initiator_service: CallInitiatorService): # Added dependency
        self.call_initiator_service = call_initiator_service # Store dependency
        self.poll_interval_s: int = app_config.TASK_SCHEDULER_POLL_INTERVAL_S
        self.is_running = False
        logger.info(f"TaskSchedulerService initialized. Poll interval: {self.poll_interval_s}s")

    async def _process_due_tasks(self):
        logger.debug("Polling for due tasks...")
        try:
            # Fetch tasks across all users (get_due_tasks can be enhanced for sharding later if needed)
            # Max tasks fetched is limited by app_config.MAX_CONCURRENT_CALLS as a practical limit
            # for how many we might try to dispatch in one poll cycle.
            due_tasks: List[Task] = await db_manager.get_due_tasks(
                user_id=None, # Poll for all users
                max_tasks=app_config.MAX_CONCURRENT_CALLS * 2 # Fetch a bit more to have a buffer
            )

            if not due_tasks:
                logger.debug("No due tasks found.")
                return

            logger.info(f"Found {len(due_tasks)} candidate due tasks to process.")

            for task in due_tasks:
                # Check if CallInitiatorService can even accept new calls before detailed processing
                if not await self.call_initiator_service.can_initiate_new_call():
                    logger.info(f"CallInitiatorService at capacity. Will retry task ID {task.id} in next poll cycle.")
                    # No status change for the task here; it will be picked up again.
                    # Break here because if initiator is full, no point checking more tasks in this cycle.
                    break 
                
                logger.info(f"Processing task ID: {task.id} for User ID: {task.user_id} (Campaign ID: {task.campaign_id})")

                is_dnd = await db_manager.is_on_dnd_list(phone_number=task.phone_number, user_id=task.user_id)
                if is_dnd:
                    logger.info(f"Task ID: {task.id} - Phone {task.phone_number} on DND for user {task.user_id}. Cancelling.")
                    await db_manager.update_task_status(
                        task_id=task.id,
                        status=TaskStatus.CANCELLED_DND,
                        overall_conclusion="Cancelled: Phone number on DND list."
                    )
                    continue

                # Task is not DND and CallInitiator has capacity.
                # Update task status to 'queued_for_call' to prevent re-picking immediately.
                # CallInitiator will update it further to 'initiating_call'.
                # This status also helps to see what TaskScheduler has handed off.
                status_updated = await db_manager.update_task_status(
                    task_id=task.id,
                    status=TaskStatus.QUEUED_FOR_CALL
                )

                if not status_updated:
                    logger.warning(f"Task ID: {task.id} - Failed to update status to '{TaskStatus.QUEUED_FOR_CALL.value}'. Skipping this cycle.")
                    continue
                
                logger.info(f"Task ID: {task.id} - Status updated to '{TaskStatus.QUEUED_FOR_CALL.value}'. Dispatching to CallInitiatorService.")
                
                # Dispatch to CallInitiatorService
                initiation_started = await self.call_initiator_service.initiate_call_for_task(task)
                
                if initiation_started:
                    logger.info(f"Task ID: {task.id} - Call initiation process started by CallInitiatorService.")
                    # CallInitiatorService will update task status to INITIATING_CALL
                else:
                    logger.warning(f"Task ID: {task.id} - CallInitiatorService did not start initiation (e.g., became full just now). Task status is '{TaskStatus.QUEUED_FOR_CALL.value}', will be re-evaluated.")
                    # Revert status or let it be picked up as QUEUED_FOR_CALL.
                    # For simplicity, let's revert it so get_due_tasks picks it cleanly if conditions change.
                    await db_manager.update_task_status(task_id=task.id, status=TaskStatus.PENDING)


        except Exception as e:
            logger.error(f"Error during task processing in TaskSchedulerService: {e}", exc_info=True)

    async def run_scheduler_loop(self):
        self.is_running = True
        logger.info("TaskSchedulerService loop started.")
        # Initial delay before first poll, allows other services (like AMI client) to potentially connect.
        await asyncio.sleep(5) 
        while self.is_running:
            try:
                await self._process_due_tasks()
            except Exception as e:
                logger.error(f"Critical error in TaskSchedulerService loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval_s * 2) # Longer sleep on critical error
            
            if self.is_running: # Check again before sleeping if stop was requested during processing
                await asyncio.sleep(self.poll_interval_s)
        logger.info("TaskSchedulerService loop stopped.")

    def stop_scheduler_loop(self):
        logger.info("TaskSchedulerService stop requested.")
        self.is_running = False

# Example of how this might be run (e.g., in main.py)
if __name__ == "__main__":
    # This __main__ block is for illustrative purposes or very basic standalone testing.
    # It would require mocks for CallInitiatorService and db_manager to run meaningfully in isolation.
    
    # class MockCallInitiator:
    #     async def can_initiate_new_call(self): return True
    #     async def initiate_call_for_task(self, task): logger.info(f"MockCallInitiator: initiate_call_for_task for {task.id}"); return True

    # async def main_ts_test():
    #     from database.db_manager import initialize_database
    #     initialize_database() 
        
    #     # Setup mock dependencies
    #     mock_initiator = MockCallInitiator()
    #     scheduler = TaskSchedulerService(call_initiator_service=mock_initiator)
        
    #     # Create some test tasks in DB manually or via db_manager for this test
    #     # ... (code to create test tasks with 'pending' status and due next_action_time) ...
        
    #     try:
    #         logger.info("Starting TaskSchedulerService test loop...")
    #         await scheduler.run_scheduler_loop()
    #     except KeyboardInterrupt:
    #         logger.info("TaskSchedulerService test interrupted.")
    #     finally:
    #         scheduler.stop_scheduler_loop()
    #         await asyncio.sleep(1) # Allow loop to exit
    #         logger.info("TaskSchedulerService test finished.")

    # asyncio.run(main_ts_test())
    logger.info("TaskSchedulerService: To test, run via main.py which sets up dependencies.")
    pass