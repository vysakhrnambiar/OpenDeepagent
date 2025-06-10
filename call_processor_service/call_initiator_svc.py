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
from database import db_manager
from database.models import Task, Call, CallCreate, CallStatus, TaskStatus # Import TaskStatus
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from call_processor_service.asterisk_ami_client import AsteriskAmiClient
from call_processor_service.call_attempt_handler import CallAttemptHandler # Import CallAttemptHandler

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class CallInitiatorService:
    def __init__(self, ami_client: AsteriskAmiClient, redis_client: RedisClient): # Added clients
        self.ami_client = ami_client
        self.redis_client = redis_client
        
        self.max_concurrent_calls: int = app_config.MAX_CONCURRENT_CALLS
        if app_config.APP_TEST_MODE:
            logger.warning(f"APPLICATION IS IN TEST MODE. Forcing max_concurrent_calls to 1.")
            self.max_concurrent_calls = 1
        
        self.active_call_attempt_ids: Set[int] = set()
        self._lock = asyncio.Lock() # Protects active_call_attempt_ids
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

        if not await self.can_initiate_new_call():
            logger.warning(f"[CallInitiator] Task ID: {task.id} - Call initiation deferred due to concurrency limit.")
            return False

        call_record: Optional[Call] = None # Define outside try for cleanup
        try:
            new_attempt_number = task.current_attempt_count + 1

            call_create_data = CallCreate(
                task_id=task.id,
                attempt_number=new_attempt_number,
                status=CallStatus.PENDING_ORIGINATION,
                prompt_used=task.generated_agent_prompt
            )
            
            call_record = await db_manager.create_call_attempt(call_create_data)

            if not call_record or not call_record.id:
                logger.error(f"[CallInitiator] Task ID: {task.id} - Failed to create DB record for call attempt. Aborting.")
                return False
            
            logger.info(f"[CallInitiator] Task ID: {task.id} - Successfully created Call Record ID: {call_record.id} for attempt {new_attempt_number}.")

            await db_manager.update_task_status(task_id=task.id, status=TaskStatus.INITIATING_CALL)

            await self._register_call_attempt(call_record.id)

            # --- Spawn CallAttemptHandler ---
            logger.info(f"[CallInitiator] Spawning CallAttemptHandler for Call ID: {call_record.id} (Task ID: {task.id})")
            handler = CallAttemptHandler(
                call_record=call_record,
                task_user_id=task.user_id,
                ami_client=self.ami_client, # Pass the AMI client instance
                redis_client=self.redis_client, # Pass the Redis client instance
                unregister_callback=self._unregister_call_attempt # Pass the callback
            )
            asyncio.create_task(handler.manage_call_lifecycle())
            # ---------------------------------

            return True

        except Exception as e:
            logger.error(f"[CallInitiator] Task ID: {task.id} - Exception during call initiation: {e}", exc_info=True)
            if call_record and call_record.id: # If call record was created before error
                 # Ensure it's unregistered if it was registered
                 async with self._lock:
                     if call_record.id in self.active_call_attempt_ids:
                        await self._unregister_call_attempt(call_record.id)
                 # Mark call as failed internally
                 await db_manager.update_call_status(call_id=call_record.id, status=CallStatus.FAILED_INTERNAL_ERROR, hangup_cause="Initiation process failure")
            
            # Revert task status to what TaskScheduler expects for retries (e.g., PENDING or RETRY_SCHEDULED)
            # TaskScheduler would have set it to QUEUED_FOR_CALL before calling this.
            # If it failed here, it means it didn't get fully processed.
            await db_manager.update_task_status(task_id=task.id, status=TaskStatus.PENDING) # Or TaskStatus.RETRY_SCHEDULED if more appropriate
            return False

# __main__ block for testing (requires more setup for clients)
if __name__ == "__main__":
    async def main_test_initiator():
        from database.db_manager import initialize_database
        # For this test to run, we'd need to initialize dummy/mock AMI and Redis clients
        # or the real ones if Asterisk/Redis are running and configured.

        class MockAmiClient: # Basic mock
            async def connect_and_login(self): logger.info("MockAmiClient: connect_and_login called"); return True
            async def send_action(self, action, **kwargs): logger.info(f"MockAmiClient: send_action called: {action}"); return {"Response": "Success"}
            def add_generic_event_listener(self, callback): logger.info("MockAmiClient: add_generic_event_listener called")
            def remove_generic_event_listener(self, callback): logger.info("MockAmiClient: remove_generic_event_listener called")
            async def close(self): logger.info("MockAmiClient: close called")

        mock_ami_client = AsteriskAmiClient() # Use the real one for structure, but it won't connect without Asterisk
        mock_redis_client = RedisClient() # Real one, assumes Redis is running or handles unavailability

        logger.info("--- Initializing Test Environment for CallInitiatorService ---")
        initialize_database()
        # It's better if dependent services (like AMI client) attempt connection when they are started
        # For now, CallInitiatorService doesn't explicitly connect them, it assumes they are usable.
        # await mock_ami_client.connect_and_login() # This would be done by a main application runner

        initiator_service = CallInitiatorService(ami_client=mock_ami_client, redis_client=mock_redis_client)

        # Setup: Create a user, campaign, and a task
        test_user = await db_manager.get_or_create_user(username="initiator_test_user")
        if not test_user: print("Failed test user create"); return
        
        campaign_data = CampaignCreate(user_id=test_user.id, batch_id="init_batch_1", user_goal_description="Initiator test campaign")
        test_campaign = await db_manager.create_campaign(campaign_data)
        if not test_campaign: print("Failed test campaign create"); return

        task_create_data = TaskCreate(
            campaign_id=test_campaign.id, user_id=test_user.id,
            user_task_description="Initiator test task",
            generated_agent_prompt="Hello from initiator test.",
            phone_number="5551237777", initial_schedule_time=datetime.now(),
            next_action_time=datetime.now(), status=TaskStatus.QUEUED_FOR_CALL # Correct initial status from TaskScheduler
        )
        # db_manager.create_task is sync, CallInitiatorService expects Task object.
        # Let's adjust test to use get_task_by_id after sync creation.
        task_id = db_manager.create_task(task_create_data) # Sync creation
        if not task_id: print("Failed test task create"); return
        
        created_task = await db_manager.get_task_by_id(task_id) # Async fetch
        if not created_task: print(f"Failed to retrieve task {task_id}"); return

        logger.info(f"--- Test: Initiating call for Task ID {created_task.id} ---")
        success = await initiator_service.initiate_call_for_task(created_task)
        logger.info(f"Call initiation attempt success: {success}")
        logger.info(f"Active calls after attempt: {await initiator_service.get_current_active_calls()}")

        if success:
            logger.info("CallAttemptHandler task created. It would manage the call and unregister upon completion.")
            logger.info("Waiting for a few seconds to simulate CallAttemptHandler activity...")
            await asyncio.sleep(5) # Give some time for the spawned (mock) handler to "run"
            # In a real scenario with a working CallAttemptHandler and AMI, the handler would call _unregister_call_attempt.
            # For this test, if the call was "successful", the active count would remain 1 until manually decremented or timeout.
            # If APP_TEST_MODE=True, only one call should have been initiated.
            
            # To test unregister, we would need to find the call_id created by initiate_call_for_task
            # and then call initiator_service._unregister_call_attempt(created_call_id)
            calls_for_task = await db_manager.get_calls_for_task(created_task.id)
            if calls_for_task:
                created_call_id = calls_for_task[0].id
                logger.info(f"Simulating CallAttemptHandler completion for Call ID: {created_call_id}")
                await initiator_service._unregister_call_attempt(created_call_id)
                logger.info(f"Active calls after simulated unregister: {await initiator_service.get_current_active_calls()}")


        logger.info("--- Test: Attempting to initiate more calls to check concurrency (if applicable) ---")
        # Example: Try to initiate more calls than allowed if not in test mode
        if not app_config.APP_TEST_MODE:
            for i in range(app_config.MAX_CONCURRENT_CALLS): # Try to fill up and exceed
                extra_task_id = db_manager.create_task(TaskCreate(
                    campaign_id=test_campaign.id, user_id=test_user.id, user_task_description=f"Extra task {i}",
                    generated_agent_prompt="Extra prompt", phone_number=f"555000{i:04d}",
                    initial_schedule_time=datetime.now(), next_action_time=datetime.now(), status=TaskStatus.QUEUED_FOR_CALL
                ))
                if extra_task_id:
                    extra_task_obj = await db_manager.get_task_by_id(extra_task_id)
                    if extra_task_obj:
                        s = await initiator_service.initiate_call_for_task(extra_task_obj)
                        logger.info(f"Extra call initiation {i+1} success: {s}. Active: {await initiator_service.get_current_active_calls()}")
                await asyncio.sleep(0.1) # Stagger a bit


        # Clean up (optional for testing, depends on how you want to leave DB)
        # await mock_redis_client.close_async_client() # If Redis client was truly async and needs closing

    # asyncio.run(main_test_initiator())
    pass