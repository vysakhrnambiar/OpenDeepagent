# Ensure all these imports are at the top of your task_manager/orchestrator_svc.py file
import sys
from pathlib import Path
import uuid
import json
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from llm_integrations.openai_form_client import OpenAIFormClient
from config.prompt_config import ORCHESTRATOR_SYSTEM_PROMPT
from database.db_manager import create_campaign, create_batch_of_tasks, update_task_hitl_info, get_task_by_id
from database.models import CampaignCreate, TaskCreate, Campaign, TaskStatus
from common.logger_setup import setup_logger
from common.redis_client import RedisClient
from common.data_models import RedisRequestUserInfoCommand

logger = setup_logger(__name__)

class OrchestratorService:
    def __init__(self, user_id: int, redis_client: Optional[RedisClient] = None):
        self.user_id = user_id
        self.llm_client = OpenAIFormClient()
        self.redis_client = redis_client or RedisClient()
        self._hitl_tasks: Dict[int, Dict[str, Any]] = {}  # Track active HITL requests
        self._hitl_listener_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._hitl_lock = asyncio.Lock()
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "schedule_call_batch",
                    "description": "Schedules a batch of calls based on the master prompt and contact list.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "master_agent_prompt": {
                                "type": "string",
                                "description": "The master template for the AI agent's instructions for all calls in this batch."
                            },
                            "contacts": {
                                "type": "array",
                                "description": "A list of contacts to call.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "The name of the person or business."},
                                        "phone": {"type": "string", "description": "The phone number of the contact."}
                                    },
                                    "required": ["name", "phone"]
                                }
                            }
                        },
                        "required": ["master_agent_prompt", "contacts"]
                    }
                }
            }
        ]

    def _schedule_call_batch(self, master_agent_prompt: str, contacts: List[Dict[str, str]]) -> str: # Returns JSON string
        logger.info(f"User ID {self.user_id}: Received request to schedule a batch of {len(contacts)} calls.")
        if not contacts:
            return json.dumps({"error_message": "Execution failed: No contacts provided."})

        campaign_goal_desc_max_len = 50000 # Max length for description in DB
        campaign_goal = master_agent_prompt[:campaign_goal_desc_max_len] + "..." if len(master_agent_prompt) > campaign_goal_desc_max_len else master_agent_prompt
        campaign_data = CampaignCreate(
            user_id=self.user_id,
            batch_id=str(uuid.uuid4()),
            user_goal_description=campaign_goal 
        )
        campaign = create_campaign(campaign_data)
        if not campaign:
            logger.error(f"User ID {self.user_id}: Failed to create campaign record in the database.")
            return json.dumps({"error_message": "Execution failed: Could not create campaign in DB."})

        tasks_to_create = []
        for contact in contacts:
            personalized_prompt = master_agent_prompt.replace("[Name]", contact.get("name", "there"))
            
            task_data = TaskCreate(
                campaign_id=campaign.id,
                user_id=self.user_id,
                user_task_description=campaign_goal,
                generated_agent_prompt=personalized_prompt,
                phone_number=contact["phone"],
                person_name=contact.get("name"),
                initial_schedule_time=datetime.now(),
                next_action_time=datetime.now(),
                max_attempts=3,  # Default max attempts
                user_info_timeout=10  # Default HITL timeout
            )
            tasks_to_create.append(task_data)

        success = create_batch_of_tasks(campaign, tasks_to_create)

        if success:
            logger.info(f"User ID {self.user_id}: Successfully created {len(tasks_to_create)} tasks for campaign {campaign.id}.")
            success_payload = {
                "status_message": f"Successfully scheduled a campaign with {len(tasks_to_create)} calls.",
                "campaign_id": campaign.id,
                "batch_id": campaign.batch_id,
                "tasks_created": len(tasks_to_create)
            }
            return json.dumps(success_payload)
        else:
            logger.error(f"User ID {self.user_id}: Failed to create batch of tasks for campaign {campaign.id}.")
            error_payload = {
                "error_message": "Execution failed: Could not create tasks in DB."
            }
            return json.dumps(error_payload)

    # THIS IS THE COMPLETE, CORRECTED execute_plan METHOD
    async def execute_plan(self, campaign_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Takes the campaign plan from the UI, uses an LLM to call the appropriate
        DB function, and executes it.
        """
        if "master_agent_prompt" not in campaign_plan or "contacts" not in campaign_plan:
             logger.warning(f"User ID {self.user_id}: Invalid campaign plan structure received: {campaign_plan}")
             return {"status": "error", "message": "Invalid campaign plan structure."}

        user_instruction_message = f"Use the 'schedule_call_batch' tool with the 'master_agent_prompt' and 'contacts' from the following campaign plan. Campaign Plan: {json.dumps(campaign_plan)}"
        
        conversation = [
            {"role": "user", "content": user_instruction_message}
        ]

        available_functions = {"schedule_call_batch": self._schedule_call_batch}

        try:
            # This method in OpenAIFormClient handles the tool call loop and returns
            # the direct string output from the executed tool (which is a JSON string from _schedule_call_batch)
            # or a final JSON string from the LLM if no tool was called or after tool iterations.
            response_from_tool_or_llm_str = await self.llm_client.generate_json_completion_with_tools(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                conversation_history=conversation,
                tools=self.tools,
                available_functions=available_functions
            )
            
            logger.debug(f"User ID {self.user_id}: Raw string response from tool/LLM process: {response_from_tool_or_llm_str}")

            try:
                # Attempt to parse the string response as JSON, as our tool _schedule_call_batch returns JSON string.
                # Also, if the LLM responded directly (less likely with this orchestrator prompt)
                # or the tool loop had an error it reported in JSON, this will parse it.
                if response_from_tool_or_llm_str is None:
                    return {"status": "error", "message": "No response from orchestrator tool/LLM."}
                parsed_data = json.loads(response_from_tool_or_llm_str)

                if isinstance(parsed_data, dict):
                    # Check if it's an error structure returned by our tool or the tool loop
                    if "error_message" in parsed_data: 
                        logger.error(f"User ID {self.user_id}: Orchestration tool reported error: {parsed_data['error_message']}")
                        return {"status": "error", "message": parsed_data['error_message']}
                    # Check if it's the success structure from our _schedule_call_batch tool
                    elif "status_message" in parsed_data and "campaign_id" in parsed_data: 
                        logger.info(f"User ID {self.user_id}: Orchestration successful. Payload: {parsed_data}")
                        return {
                            "status": "success", 
                            "message": parsed_data['status_message'],
                            "data": parsed_data # Pass along the full success payload from the tool
                        }
                    # Check if it's a generic error status from the LLM/tool loop in OpenAIFormClient
                    elif parsed_data.get("status") == "error" and "message" in parsed_data:
                        logger.error(f"User ID {self.user_id}: Orchestration LLM/tool loop error: {parsed_data['message']}")
                        return parsed_data # Forward the error structure
                    else: 
                        # Some other JSON structure came back unexpectedly
                        logger.warning(f"User ID {self.user_id}: Orchestration process returned unexpected JSON: {parsed_data}")
                        return {"status": "error", "message": "Orchestration returned an unexpected JSON structure."}
                else: 
                    # The string parsed to JSON, but wasn't a dictionary (e.g. just a string "null" or a number)
                    logger.warning(f"User ID {self.user_id}: Orchestration process returned non-dictionary JSON: {parsed_data}")
                    return {"status": "error", "message": f"Orchestration response was not a dictionary: {str(parsed_data)}"}

            except (json.JSONDecodeError, TypeError) as e:
                # This means response_from_tool_or_llm_str was not a valid JSON string at all.
                # This is less likely if the tool _schedule_call_batch is called, as it returns JSON strings.
                # Could happen if the LLM gives a plain text response instead of calling the tool.
                logger.error(f"User ID {self.user_id}: Failed to parse JSON from orchestrator tool/LLM response. Raw: '{response_from_tool_or_llm_str}'. Error: {e}")
                # If the string itself indicates success (fallback for safety, though ideally tool returns JSON)
                if response_from_tool_or_llm_str and "Successfully scheduled" in response_from_tool_or_llm_str:
                     logger.info(f"User ID {self.user_id}: Orchestration succeeded based on string content (though JSON parse failed). Response: {response_from_tool_or_llm_str}")
                     return {"status": "success", "message": response_from_tool_or_llm_str}

                return {"status": "error", "message": "Orchestrator did not return a valid structured response from tool execution."}

        except Exception as e:
            logger.error(f"User ID {self.user_id}: A critical error occurred during plan execution: {e}", exc_info=True)
            return {"status": "error", "message": f"Server error during orchestration: {str(e)}"}

    async def start_hitl_listener(self):
        """Start the Redis listener for HITL commands"""
        if self._hitl_listener_task and not self._hitl_listener_task.done():
            logger.warning(f"HITL listener already running for user {self.user_id}")
            return
        
        self._stop_event.clear()
        self._hitl_listener_task = asyncio.create_task(self._hitl_redis_listener())
        logger.info(f"Started HITL listener for user {self.user_id}")

    async def stop_hitl_listener(self):
        """Stop the Redis listener for HITL commands"""
        self._stop_event.set()
        if self._hitl_listener_task and not self._hitl_listener_task.done():
            self._hitl_listener_task.cancel()
            try:
                await self._hitl_listener_task
            except asyncio.CancelledError:
                pass
        logger.info(f"Stopped HITL listener for user {self.user_id}")

    async def _hitl_redis_listener(self):
        """Listen for HITL commands on Redis and process them"""
        if not self.redis_client:
            logger.warning(f"No Redis client available for HITL listener user {self.user_id}")
            return
            
        try:
            # Use the existing subscribe_to_channel method
            pattern = "call_commands:*"
            await self.redis_client.subscribe_to_channel(pattern, self._redis_message_callback)
                    
        except asyncio.CancelledError:
            logger.info(f"HITL listener cancelled for user {self.user_id}")
        except Exception as e:
            logger.error(f"Fatal error in HITL listener for user {self.user_id}: {e}", exc_info=True)

    async def _redis_message_callback(self, channel: str, data: dict):
        """Callback for Redis messages"""
        try:
            if isinstance(data, dict) and data.get("command_type") == "request_user_info":
                await self._handle_hitl_request(data)
        except Exception as e:
            logger.error(f"Error in Redis message callback: {e}", exc_info=True)

    async def _handle_hitl_request(self, command_data: Dict[str, Any]):
        """Handle incoming request_user_info command"""
        try:
            call_attempt_id = command_data.get("call_attempt_id")
            question = command_data.get("question", "")
            timeout_seconds = command_data.get("timeout_seconds", 10)
            recipient_message = command_data.get("recipient_message", "")
            
            if not call_attempt_id or not isinstance(call_attempt_id, int):
                logger.error(f"Invalid call_attempt_id in HITL request: {call_attempt_id}")
                return
            
            logger.info(f"Processing HITL request for call {call_attempt_id}: {question}")
            
            # Get task information
            # First we need to get task_id from call_attempt_id
            from database.db_manager import get_call_by_id
            call_record = get_call_by_id(call_attempt_id)
            if not call_record:
                logger.error(f"Call record not found for ID {call_attempt_id}")
                return
            
            task = get_task_by_id(call_record.task_id)
            if not task:
                logger.error(f"Task not found for call {call_attempt_id}")
                return
            
            # Update task with HITL request info
            request_time = datetime.now()
            success = update_task_hitl_info(
                task_id=task.id,
                user_info_request=question,
                user_info_requested_at=request_time,
                status=TaskStatus.PENDING_USER_INFO
            )
            
            if not success:
                logger.error(f"Failed to update task {task.id} with HITL request")
                return
            
            # Store HITL request for timeout tracking
            self._hitl_tasks[task.id] = {
                "call_attempt_id": call_attempt_id,
                "question": question,
                "timeout_seconds": timeout_seconds,
                "recipient_message": recipient_message,
                "request_time": request_time,
                "timeout_task": asyncio.create_task(self._handle_hitl_timeout(task.id, timeout_seconds))
            }
            
            # Send HITL notification to the specific user who created the task
            await self._send_hitl_notification(task, question, timeout_seconds, recipient_message)
            
            logger.info(f"HITL request stored for task {task.id}, timeout in {timeout_seconds}s")
            
        except Exception as e:
            logger.error(f"Error handling HITL request: {e}", exc_info=True)

    async def _handle_hitl_timeout(self, task_id: int, timeout_seconds: int):
        """Handle timeout for HITL request with proper context injection"""
        try:
            await asyncio.sleep(timeout_seconds)
            
            async with self._hitl_lock:
                # Check if task creator has responded (task removed from tracking means response received)
                if task_id not in self._hitl_tasks:
                    logger.debug(f"HITL task {task_id} already handled, skipping timeout processing")
                    return
                    
                hitl_info = self._hitl_tasks[task_id]
            call_attempt_id = hitl_info["call_attempt_id"]
            
            # Task creator didn't respond within timeout
            logger.info(f"HITL timeout for task {task_id}, injecting context and triggering call termination")
            
            # STEP 1: Inject system message to give AI context about the timeout
            from common.data_models import RedisInjectSystemMessageCommand, RedisEndCallCommand
            
            timeout_message = (
                "The task creator didn't respond within the timeout period. "
                "Please inform the call recipient that you need to get some additional information "
                "and will call them back shortly with the information they need, then end the call politely."
            )
            
            # Instead of injecting system message, publish HITL timeout event
            # The OpenAI client will handle the system message injection
            from common.data_models import RedisHITLTimeoutCommand
            
            timeout_command = RedisHITLTimeoutCommand(
                call_attempt_id=call_attempt_id,
                question=hitl_info.get("question", "")
            )
            
            channel = f"hitl_events:{call_attempt_id}"
            await self.redis_client.publish_command(channel, timeout_command.model_dump())
            
            logger.info(f"Published HITL timeout event for call {call_attempt_id}")
            
            # STEP 2: Wait a moment for AI to process the context and respond
            await asyncio.sleep(3)
            
            # STEP 3: Send graceful termination command (AI now has context)
            termination_command = RedisEndCallCommand(
                call_attempt_id=call_attempt_id,
                reason="Waiting for task creator information - will call back when available",
                outcome="user_busy",  # Using user_busy as closest match for "waiting for info"
                final_message="I need to get some additional information. I'll call you back shortly with what you need."
            )
            
            # Publish termination command to the correct channel
            termination_channel = f"call_commands:{call_attempt_id}"
            await self.redis_client.publish_command(termination_channel, termination_command.model_dump())
            
            logger.info(f"Sent graceful termination command for call {call_attempt_id}")
            
            # Clean up HITL tracking after timeout
            if task_id in self._hitl_tasks:
                del self._hitl_tasks[task_id]
            
            # Send timeout notification to user
            try:
                from web_interface.app import hitl_manager
                from database.db_manager import get_db_connection, get_task_by_id
                
                # Get task info to find user_id
                task = get_task_by_id(task_id)
                if not task:
                    logger.error(f"Task {task_id} not found for timeout notification")
                    return
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT username FROM users WHERE id = ?", (task.user_id,))
                    user_row = cursor.fetchone()
                    if user_row:
                        username = user_row[0]
                        timeout_message = {
                            "type": "hitl_timeout",
                            "task_id": task_id
                        }
                        await hitl_manager.send_to_user(username, timeout_message)
                finally:
                    conn.close()
            except Exception as e:
                logger.error(f"Error sending timeout notification: {e}")
                
        except asyncio.CancelledError:
            logger.debug(f"HITL timeout cancelled for task {task_id} (likely due to task creator response)")
        except Exception as e:
            logger.error(f"Error in HITL timeout handling for task {task_id}: {e}", exc_info=True)

    async def handle_task_creator_response(self, task_id: int, response: str) -> bool:
        """Handle response from task creator"""
        async with self._hitl_lock:
            try:
                task = get_task_by_id(task_id)
                if not task:
                    logger.error(f"Task {task_id} not found for task creator response")
                    return False
                
                if task.status != TaskStatus.PENDING_USER_INFO:
                    logger.warning(f"Task {task_id} not in PENDING_USER_INFO status, ignoring response")
                    return False
                
                # Check if this is within timeout or after timeout
                within_timeout = task_id in self._hitl_tasks
                
                if within_timeout:
                    # Response within timeout - inject into live call
                    hitl_info = self._hitl_tasks[task_id]
                    call_attempt_id = hitl_info["call_attempt_id"]
                    original_question = hitl_info.get("question", "")
                    
                    # Cancel the timeout task
                    if hitl_info["timeout_task"]:
                        hitl_info["timeout_task"].cancel()
                    
                    # Update task with response and reset to previous status
                    update_task_hitl_info(
                        task_id=task_id,
                        user_info_response=response,
                        status=TaskStatus.QUEUED_FOR_CALL  # Return to active call state
                    )
                    
                    # Publish HITL response event to the correct channel
                    from common.data_models import RedisHITLResponseCommand
                    
                    response_command = RedisHITLResponseCommand(
                        call_attempt_id=call_attempt_id,
                        response=response
                    )
                    
                    channel = f"hitl_events:{call_attempt_id}"
                    success = await self.redis_client.publish_command(channel, response_command.model_dump())
                    
                    if success:
                        logger.info(f"Task creator response received within timeout for task {task_id}, successfully published to call {call_attempt_id}")
                    else:
                        logger.error(f"Failed to publish task creator response to call {call_attempt_id}")
                    
                    # Clean up HITL tracking
                    del self._hitl_tasks[task_id]
                    
                else:
                    # Response after timeout - schedule new call
                    update_task_hitl_info(
                        task_id=task_id,
                        user_info_response=response,
                        status=TaskStatus.PENDING  # Reset to pending for new call
                    )
                    
                    # Schedule new call with the provided information
                    from database.db_manager import update_task_status
                    update_task_status(
                        task_id=task_id,
                        status=TaskStatus.PENDING,
                        next_action_time=datetime.now() + timedelta(minutes=1)  # Schedule for 1 minute from now
                    )
                    
                    logger.info(f"Task creator response received after timeout for task {task_id}, new call scheduled")
                
                return True
            except Exception as e:
                logger.error(f"Error handling task creator response for task {task_id}: {e}", exc_info=True)
                return False

    async def _send_hitl_notification(self, task, question: str, timeout_seconds: int, recipient_message: str):
        """Send HITL notification to the specific user who created the task"""
        try:
            # Get the username for this user_id
            from database.db_manager import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("SELECT username FROM users WHERE id = ?", (task.user_id,))
                user_row = cursor.fetchone()
                
                if not user_row:
                    logger.error(f"User not found for task {task.id} (user_id: {task.user_id})")
                    return
                
                username = user_row[0]
                
                # Try to get the WebSocket manager from the FastAPI app
                try:
                    from web_interface.app import hitl_manager
                    
                    # Create notification message
                    notification_message = {
                        "type": "hitl_request",
                        "task_id": task.id,
                        "question": question,
                        "timeout_seconds": timeout_seconds,
                        "recipient_message": recipient_message,
                        "call_info": {
                            "phone_number": task.phone_number,
                            "person_name": task.person_name or "Unknown",
                            "business_name": getattr(task, 'business_name', None)
                        }
                    }
                    
                    # Send notification to the specific user
                    await hitl_manager.send_to_user(username, notification_message)
                    logger.info(f"Sent HITL notification to user {username} for task {task.id}")
                    
                except ImportError as e:
                    logger.error(f"Could not import WebSocket manager: {e}")
                except Exception as e:
                    logger.error(f"Error sending WebSocket notification: {e}", exc_info=True)
                    
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"Error sending HITL notification for task {task.id}: {e}", exc_info=True)