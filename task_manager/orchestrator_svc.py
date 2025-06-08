# Ensure all these imports are at the top of your task_manager/orchestrator_svc.py file
import sys
from pathlib import Path
import uuid
import json 
from typing import List, Dict, Any
from datetime import datetime 

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from llm_integrations.openai_form_client import OpenAIFormClient
from config.prompt_config import ORCHESTRATOR_SYSTEM_PROMPT
from database.db_manager import create_campaign, create_batch_of_tasks
from database.models import CampaignCreate, TaskCreate, Campaign 
from common.logger_setup import setup_logger

logger = setup_logger(__name__)

class OrchestratorService:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.llm_client = OpenAIFormClient()
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

        campaign_goal = "Campaign goal derived from user interaction (can be enhanced)." 
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
                user_task_description=campaign_goal, 
                generated_agent_prompt=personalized_prompt,
                phone_number=contact["phone"],
                person_name=contact.get("name"),
                initial_schedule_time=datetime.now(), 
                next_action_time=datetime.now() 
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

        user_instruction_message = f"Please schedule the campaign defined by the following plan: {json.dumps(campaign_plan)}"
        
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
                if "Successfully scheduled" in response_from_tool_or_llm_str:
                     logger.info(f"User ID {self.user_id}: Orchestration succeeded based on string content (though JSON parse failed). Response: {response_from_tool_or_llm_str}")
                     return {"status": "success", "message": response_from_tool_or_llm_str}

                return {"status": "error", "message": "Orchestrator did not return a valid structured response from tool execution."}

        except Exception as e:
            logger.error(f"User ID {self.user_id}: A critical error occurred during plan execution: {e}", exc_info=True)
            return {"status": "error", "message": f"Server error during orchestration: {str(e)}"}