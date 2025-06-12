# call_processor_service/call_initiator_svc.py

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import Set, Optional 

# --- Path Setup ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Setup ---

from config.app_config import app_config
from database import db_manager # db_manager's functions will be called directly
from database.models import Task, Call, CallCreate, CallStatus, TaskStatus
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from call_processor_service.asterisk_ami_client import AsteriskAmiClient
from call_processor_service.call_attempt_handler import CallAttemptHandler

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class CallInitiatorService:
    def __init__(self, ami_client: AsteriskAmiClient, redis_client: RedisClient):
        self.ami_client = ami_client
        self.redis_client = redis_client
        
        self.max_concurrent_calls: int = app_config.MAX_CONCURRENT_CALLS
        if app_config.APP_TEST_MODE:
            logger.warning(f"APPLICATION IS IN TEST MODE. Forcing max_concurrent_calls to 1.")
            self.max_concurrent_calls = 1
        
        self.active_call_attempt_ids: Set[int] = set()
        self._lock = asyncio.Lock()
        # self._loop is no longer needed here if db_manager functions are async def
        logger.info(f"CallInitiatorService initialized. Effective max concurrent calls: {self.max_concurrent_calls}")

    async def _register_call_attempt(self, call_id: int):
        async with self._lock:
            self.active_call_attempt_ids.add(call_id)
            logger.debug(f"[CallInitiator] Registered active call attempt ID: {call_id}. Current count: {len(self.active_call_attempt_ids)}")

    async def _unregister_call_attempt(self, call_id: int):
        async with self._lock:
            self.active_call_attempt_ids.discard(call_id)
            logger.info(f"[CallInitiator] Unregistered call attempt ID: {call_id}. Current active calls: {len(self.active_call_attempt_ids)}")

    async def get_current_active_calls(self) -> int:
        async with self._lock:
            return len(self.active_call_attempt_ids)

    async def can_initiate_new_call(self) -> bool:
        current_active = await self.get_current_active_calls()
        can_initiate = current_active < self.max_concurrent_calls
        if not can_initiate:
            logger.info(f"[CallInitiator] Cannot initiate new call. Concurrency limit reached ({current_active}/{self.max_concurrent_calls}).")
        return can_initiate

    async def initiate_call_for_task(self, task: Task) -> bool:
        logger.info(f"[CallInitiator] Attempting to initiate call for Task ID: {task.id} (User ID: {task.user_id}, Phone: {task.phone_number})")
        loop = asyncio.get_running_loop() # <<< DEFINE LOOP HERE, AT THE START OF THE METHOD


        if not await self.can_initiate_new_call():
            logger.warning(f"[CallInitiator] Task ID: {task.id} - Call initiation deferred due to concurrency limit.")
            return False

        call_record: Optional[Call] = None # Renamed from call_record_sync
        try:
            new_attempt_number = task.current_attempt_count + 1

            call_create_data = CallCreate(
                task_id=task.id,
                attempt_number=new_attempt_number,
                status=CallStatus.PENDING_ORIGINATION,
                prompt_used=task.generated_agent_prompt
            )
                        # --- START ADDED DEBUGGING ---
            logger.debug(f"[CallInitiator] About to call db_manager.create_call_attempt.")
            logger.debug(f"[CallInitiator] Type of db_manager.create_call_attempt: {type(db_manager.create_call_attempt)}")
            logger.debug(f"[CallInitiator] Is db_manager.create_call_attempt a coroutine function? {asyncio.iscoroutinefunction(db_manager.create_call_attempt)}")
            # --- END ADDED DEBUGGING ---

            # Directly await db_manager.create_call_attempt as it's async def
            call_record = await loop.run_in_executor(
                None,
                db_manager.create_call_attempt, # This is now a sync def function
                call_create_data
            )
                        # --- START ADDED DEBUGGING ---
            logger.debug(f"[CallInitiator] Returned from db_manager.create_call_attempt via executor.")
            logger.debug(f"[CallInitiator] Type of call_record: {type(call_record)}")
            logger.debug(f"[CallInitiator] Value of call_record (repr): {call_record!r}")
            logger.debug(f"[CallInitiator] Is call_record a coroutine object? {asyncio.iscoroutine(call_record)}")
            # --- END ADDED DEBUGGING ---

            if not call_record or not call_record.id:
                logger.error(f"[CallInitiator] Task ID: {task.id} - Failed to create DB record for call attempt. Aborting.")
                return False
            
            logger.info(f"[CallInitiator] Task ID: {task.id} - Successfully created Call Record ID: {call_record.id} for attempt {new_attempt_number}.")

            # Directly await db_manager.update_task_status as it's async def
            await loop.run_in_executor(
                None,
                db_manager.update_task_status, # This is a sync def function
                task.id, 
                TaskStatus.INITIATING_CALL
            )

            await self._register_call_attempt(call_record.id)

            logger.info(f"[CallInitiator] Spawning CallAttemptHandler for Call ID: {call_record.id} (Task ID: {task.id})")
            handler = CallAttemptHandler(
                call_record=call_record, # Use the awaited call_record
                task_user_id=task.user_id,
                ami_client=self.ami_client,
                redis_client=self.redis_client,
                unregister_callback=self._unregister_call_attempt
            )
            asyncio.create_task(handler.manage_call_lifecycle())
            return True

        except Exception as e:
            logger.error(f"[CallInitiator] Task ID: {task.id} - Exception during call initiation: {e}", exc_info=True)
            if call_record and call_record.id:
                async with self._lock:
                    if call_record.id in self.active_call_attempt_ids:
                       await self._unregister_call_attempt(call_record.id)
                logger.info(f"[CallInitiator] Attempting to update call_record {call_record.id} to FAILED_INTERNAL_ERROR") # New Log
                update_call_success = await loop.run_in_executor( # Use executor for sync update_call_status
                    None,
                    db_manager.update_call_status,
                    call_record.id,
                    CallStatus.FAILED_INTERNAL_ERROR, 
                    "Initiation process failure"
                )
                logger.info(f"[CallInitiator] Update call_record status success: {update_call_success}") # New Log
            else: # Add an else to log if call_record was not valid
                logger.warning(f"[CallInitiator] call_record not valid or has no id in except block. call_record: {call_record!r}")    
            revert_task_success = await loop.run_in_executor(
                None,
                db_manager.update_task_status,
                task.id, 
                TaskStatus.PENDING 
            )
            logger.info(f"[CallInitiator] Reverted Task ID {task.id} to PENDING status success: {revert_task_success}") # New Log
            return False