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
from database import db_manager # db_manager itself
from database.models import Task, TaskStatus
from common.logger_setup import setup_logger
from call_processor_service.call_initiator_svc import CallInitiatorService

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class TaskSchedulerService:
    def __init__(self, call_initiator_service: CallInitiatorService):
        self.call_initiator_service = call_initiator_service
        self.poll_interval_s: int = app_config.TASK_SCHEDULER_POLL_INTERVAL_S
        self.is_running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None # Store the loop
        logger.info(f"TaskSchedulerService initialized. Poll interval: {self.poll_interval_s}s")

    async def _process_due_tasks(self):
        logger.debug("Polling for due tasks...")
        if self._loop is None: # Ensure loop is available
            self._loop = asyncio.get_running_loop()

        try:
            # Run the synchronous db_manager.get_due_tasks in an executor
            due_tasks: List[Task] = await self._loop.run_in_executor(
                None, # Uses the default ThreadPoolExecutor
                db_manager.get_due_tasks, # The function to call
                None, # arg1 for get_due_tasks (user_id)
                app_config.MAX_CONCURRENT_CALLS * 2 # arg2 for get_due_tasks (max_tasks)
            )

            if not due_tasks:
                logger.debug("No due tasks found.")
                return

            logger.info(f"Found {len(due_tasks)} candidate due tasks to process.")

            for task in due_tasks:
                if not await self.call_initiator_service.can_initiate_new_call():
                    logger.info(f"CallInitiatorService at capacity. Will retry task ID {task.id} in next poll cycle.")
                    break 
                
                logger.info(f"Processing task ID: {task.id} for User ID: {task.user_id} (Campaign ID: {task.campaign_id})")

                # Run synchronous db_manager.is_on_dnd_list in executor
                is_dnd = await self._loop.run_in_executor(
                    None,
                    db_manager.is_on_dnd_list,
                    task.phone_number,
                    task.user_id
                )

                if is_dnd:
                    logger.info(f"Task ID: {task.id} - Phone {task.phone_number} on DND for user {task.user_id}. Cancelling.")
                    # Run synchronous db_manager.update_task_status in executor
                    await self._loop.run_in_executor(
                        None,
                        db_manager.update_task_status, # Function
                        task.id,                      # arg1: task_id
                        TaskStatus.CANCELLED_DND,     # arg2: status
                        None,                         # arg3: next_action_time (default None)
                        "Cancelled: Phone number on DND list." # arg4: overall_conclusion
                        # increment_attempt_count is False by default
                    )
                    continue

                # Run synchronous db_manager.update_task_status in executor
                status_updated = await self._loop.run_in_executor(
                    None,
                    db_manager.update_task_status,
                    task.id,
                    TaskStatus.QUEUED_FOR_CALL
                )

                if not status_updated:
                    logger.warning(f"Task ID: {task.id} - Failed to update status to '{TaskStatus.QUEUED_FOR_CALL.value}'. Skipping this cycle.")
                    continue
                
                logger.info(f"Task ID: {task.id} - Status updated to '{TaskStatus.QUEUED_FOR_CALL.value}'. Dispatching to CallInitiatorService.")
                
                initiation_started = await self.call_initiator_service.initiate_call_for_task(task)
                
                if initiation_started:
                    logger.info(f"Task ID: {task.id} - Call initiation process started by CallInitiatorService.")
                else:
                    logger.warning(f"Task ID: {task.id} - CallInitiatorService did not start initiation. Reverting status.")
                    # Run synchronous db_manager.update_task_status in executor
                    await self._loop.run_in_executor(
                        None,
                        db_manager.update_task_status,
                        task.id,
                        TaskStatus.PENDING # Revert to PENDING
                    )

        except Exception as e:
            logger.error(f"Error during task processing in TaskSchedulerService: {e}", exc_info=True)

    async def run_scheduler_loop(self):
        self.is_running = True
        self._loop = asyncio.get_running_loop() # Get loop when scheduler starts
        logger.info("TaskSchedulerService loop started.")
        await asyncio.sleep(5) 
        while self.is_running:
            try:
                await self._process_due_tasks()
            except Exception as e:
                logger.error(f"Critical error in TaskSchedulerService loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval_s * 2)
            
            if self.is_running:
                await asyncio.sleep(self.poll_interval_s)
        logger.info("TaskSchedulerService loop stopped.")

    def stop_scheduler_loop(self):
        logger.info("TaskSchedulerService stop requested.")
        self.is_running = False