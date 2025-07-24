import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import math

# Path setup
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from database.db_manager import (
    get_call_by_id, get_task_by_id, update_task_status, 
    get_db_connection, update_call_status
)
from database.models import TaskStatus, CallStatus, TaskEventCreate

logger = setup_logger(__name__)

class PostCallAnalyzerService:
    """
    Service that analyzes completed calls and determines if retries are needed.
    Handles automatic retry scheduling with exponential backoff.
    """
    
    def __init__(self, redis_client: Optional[RedisClient] = None):
        self.redis_client = redis_client or RedisClient()
        self._stop_event = asyncio.Event()
        self._listener_task: Optional[asyncio.Task] = None
        
        # Retry configuration
        self.base_retry_delay_minutes = 5  # Base delay between retries
        self.max_retry_delay_minutes = 240  # Maximum delay (4 hours)
        self.retry_multiplier = 2  # Exponential backoff multiplier
        
        # Call outcome classifications
        self.retriable_outcomes = {
            CallStatus.FAILED_NO_ANSWER,
            CallStatus.FAILED_BUSY,
            CallStatus.FAILED_CONGESTION,
            CallStatus.FAILED_CHANNEL_UNAVAILABLE
        }
        
        self.non_retriable_outcomes = {
            CallStatus.FAILED_INVALID_NUMBER,
            CallStatus.COMPLETED_AI_OBJECTIVE_MET,
            CallStatus.COMPLETED_USER_HANGUP,
            CallStatus.COMPLETED_AI_HANGUP
        }

    async def start(self):
        """Start the post-call analyzer service"""
        if self._listener_task and not self._listener_task.done():
            logger.warning("Post-call analyzer already running")
            return
        
        self._stop_event.clear()
        self._listener_task = asyncio.create_task(self._redis_listener())
        logger.info("Post-call analyzer service started")

    async def stop(self):
        """Stop the post-call analyzer service"""
        self._stop_event.set()
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        logger.info("Post-call analyzer service stopped")

    async def _redis_listener(self):
        """Listen for call completion events on Redis"""
        try:
            # Subscribe to call completion events
            pattern = "call_completed:*"
            await self.redis_client.subscribe_to_channel(pattern, self._handle_call_completion)
            
        except asyncio.CancelledError:
            logger.info("Post-call analyzer listener cancelled")
        except Exception as e:
            logger.error(f"Error in post-call analyzer listener: {e}", exc_info=True)

    async def _handle_call_completion(self, channel: str, data: Dict[str, Any]):
        """Handle call completion event from Redis"""
        try:
            call_id = data.get("call_id")
            if not call_id:
                logger.error("No call_id in call completion event")
                return
            
            logger.info(f"Processing call completion for call {call_id}")
            
            # Get call and task information
            call = get_call_by_id(call_id)
            if not call:
                logger.error(f"Call {call_id} not found in database")
                return
            
            task = get_task_by_id(call.task_id)
            if not task:
                logger.error(f"Task {call.task_id} not found for call {call_id}")
                return
            
            # Analyze call outcome and determine next action
            await self._analyze_call_outcome(call, task)
            
        except Exception as e:
            logger.error(f"Error handling call completion: {e}", exc_info=True)

    async def _analyze_call_outcome(self, call, task):
        """Analyze call outcome and determine if retry is needed"""
        try:
            call_status = CallStatus(call.status)
            logger.info(f"Analyzing call {call.id} with status {call_status}")
            
            # Log task event
            await self._log_task_event(
                task.id,
                "call_completed",
                {
                    "call_id": call.id,
                    "call_status": call_status.value,
                    "attempt_number": call.attempt_number,
                    "duration_seconds": call.duration_seconds
                }
            )
            
            # Check if call was successful
            if call_status in [CallStatus.COMPLETED_AI_OBJECTIVE_MET]:
                await self._handle_successful_call(call, task)
                return
            
            # Check if call outcome is non-retriable
            if call_status in self.non_retriable_outcomes:
                await self._handle_non_retriable_failure(call, task)
                return
            
            # Check if call outcome is retriable
            if call_status in self.retriable_outcomes:
                await self._handle_retriable_failure(call, task)
                return
            
            # Handle unknown or system error outcomes
            await self._handle_unknown_outcome(call, task)
            
        except Exception as e:
            logger.error(f"Error analyzing call outcome for call {call.id}: {e}", exc_info=True)

    async def _handle_successful_call(self, call, task):
        """Handle successful call completion"""
        logger.info(f"Call {call.id} completed successfully")
        
        # Update task status to completed
        update_task_status(
            task_id=task.id,
            status=TaskStatus.COMPLETED_SUCCESS,
            next_action_time=None
        )
        
        await self._log_task_event(
            task.id,
            "task_completed_success",
            {
                "call_id": call.id,
                "reason": "AI objective met",
                "total_attempts": task.current_attempt_count + 1
            }
        )

    async def _handle_non_retriable_failure(self, call, task):
        """Handle non-retriable call failure"""
        logger.info(f"Call {call.id} failed with non-retriable outcome: {call.status}")
        
        # Update task status to failed
        update_task_status(
            task_id=task.id,
            status=TaskStatus.COMPLETED_FAILURE,
            next_action_time=None
        )
        
        await self._log_task_event(
            task.id,
            "task_completed_failure",
            {
                "call_id": call.id,
                "reason": f"Non-retriable failure: {call.status}",
                "total_attempts": task.current_attempt_count + 1
            }
        )

    async def _handle_retriable_failure(self, call, task):
        """Handle retriable call failure - check if retry is possible"""
        logger.info(f"Call {call.id} failed with retriable outcome: {call.status}")
        
        # Check if we've reached max attempts
        current_attempts = task.current_attempt_count + 1  # Include current attempt
        
        if current_attempts >= task.max_attempts:
            logger.info(f"Task {task.id} reached max attempts ({task.max_attempts})")
            
            # Update task status to failed
            update_task_status(
                task_id=task.id,
                status=TaskStatus.COMPLETED_FAILURE,
                next_action_time=None
            )
            
            await self._log_task_event(
                task.id,
                "task_completed_failure",
                {
                    "call_id": call.id,
                    "reason": f"Max attempts reached: {call.status}",
                    "total_attempts": current_attempts
                }
            )
        else:
            # Schedule retry
            await self._schedule_retry(call, task, current_attempts)

    async def _schedule_retry(self, call, task, current_attempts):
        """Schedule a retry for the task with exponential backoff"""
        try:
            # Calculate retry delay with exponential backoff
            delay_minutes = self._calculate_retry_delay(current_attempts)
            next_retry_time = datetime.now() + timedelta(minutes=delay_minutes)
            
            logger.info(f"Scheduling retry for task {task.id} in {delay_minutes} minutes (attempt {current_attempts + 1}/{task.max_attempts})")
            
            # Update task for retry
            conn = get_db_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    UPDATE tasks 
                    SET status = ?, 
                        next_action_time = ?, 
                        current_attempt_count = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    TaskStatus.RETRY_SCHEDULED.value,
                    next_retry_time,
                    current_attempts,
                    task.id
                ))
                
                conn.commit()
                
                await self._log_task_event(
                    task.id,
                    "retry_scheduled",
                    {
                        "call_id": call.id,
                        "previous_status": call.status,
                        "attempt_number": current_attempts,
                        "next_retry_time": next_retry_time.isoformat(),
                        "delay_minutes": delay_minutes
                    }
                )
                
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"Error scheduling retry for task {task.id}: {e}", exc_info=True)

    async def _handle_unknown_outcome(self, call, task):
        """Handle unknown or system error outcomes"""
        logger.warning(f"Call {call.id} has unknown outcome: {call.status}")
        
        # Treat as retriable failure for now
        await self._handle_retriable_failure(call, task)

    def _calculate_retry_delay(self, attempt_number: int) -> int:
        """Calculate retry delay with exponential backoff"""
        # Exponential backoff: base_delay * (multiplier ^ (attempt - 1))
        delay_minutes = self.base_retry_delay_minutes * (self.retry_multiplier ** (attempt_number - 1))
        
        # Cap at maximum delay
        delay_minutes = min(delay_minutes, self.max_retry_delay_minutes)
        
        # Add some jitter to prevent thundering herd
        jitter = delay_minutes * 0.1 * (hash(str(attempt_number)) % 100 / 100)
        delay_minutes += jitter
        
        return int(delay_minutes)

    async def _log_task_event(self, task_id: int, event_type: str, event_details: Dict[str, Any]):
        """Log a task event to the database"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT INTO task_events (task_id, event_type, event_details, created_by)
                    VALUES (?, ?, ?, ?)
                """, (
                    task_id,
                    event_type,
                    json.dumps(event_details),
                    "post_call_analyzer"
                ))
                
                conn.commit()
                logger.debug(f"Logged task event: {event_type} for task {task_id}")
                
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"Error logging task event for task {task_id}: {e}", exc_info=True)

# Example usage for testing
async def main():
    analyzer = PostCallAnalyzerService()
    try:
        await analyzer.start()
        logger.info("Post-call analyzer service is running...")
        
        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down post-call analyzer service...")
    finally:
        await analyzer.stop()

if __name__ == "__main__":
    asyncio.run(main())