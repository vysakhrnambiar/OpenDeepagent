import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from llm_integrations.openai_form_client import OpenAIFormClient
from config.prompt_config import UI_ASSISTANT_SYSTEM_PROMPT
from common.logger_setup import setup_logger
# Import the tool functions
from tools.information_retriever_svc import search_internet, get_authoritative_business_info

logger = setup_logger(__name__)

class UIAssistantService:
    # MODIFIED: __init__ now accepts username
    def __init__(self, username: str):
        self.username = username # Store the username
        self.llm_client = OpenAIFormClient()
        self.system_prompt = UI_ASSISTANT_SYSTEM_PROMPT # Ensure this prompt handles username if needed, or pass it contextually
        
        # Define available tools for the UI assistant
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_internet",
                    "description": "Searches the internet for general information, products, or non-business specific queries. Use this for broad research.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query to find information on the internet."
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_authoritative_business_info",
                    "description": "Gets authoritative information (phone number, address, hours) for a specific business. Use this when you need reliable contact details for a known business.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "business_name": {
                                "type": "string",
                                "description": "The name of the business."
                            },
                            "location_context": {
                                "type": "string",
                                "description": "The city, state, or general area of the business to help narrow down the search (e.g., 'Kigali, Rwanda', 'San Francisco, CA')."
                            }
                        },
                        "required": ["business_name", "location_context"]
                    }
                }
            }
        ]
        self.available_functions = {
            "search_internet": search_internet,
            "get_authoritative_business_info": get_authoritative_business_info
        }

    def _is_plan_valid(self, plan: Dict[str, Any]) -> bool:
        """
        Validates the generated campaign plan.
        Specifically checks if all contacts have a phone number.
        """
        if not plan or "contacts" not in plan or not isinstance(plan["contacts"], list):
            logger.warning("Plan validation failed: 'contacts' array is missing or invalid.")
            return False
        if not plan["contacts"]: # Empty contact list might be valid if the goal is different
            # logger.info("Plan validation: Contact list is empty. Assuming this is intended for the current goal.")
            return True # Or False if contacts are always required. For now, allow.

        for contact in plan["contacts"]:
            if not isinstance(contact, dict) or "phone" not in contact or not contact["phone"]:
                logger.warning(f"Plan validation failed: Contact missing phone number. Contact: {contact}")
                return False
            # Basic check for phone-like characters (digits, +, -, (, ), space)
            # This is not a strict validation but catches obvious errors like "undefined"
            if not all(c.isdigit() or c in ['+', '-', '(', ')', ' '] for c in str(contact["phone"])):
                logger.warning(f"Plan validation failed: Invalid characters in phone number for contact {contact.get('name', 'N/A')}. Phone: {contact['phone']}")
                return False
        return True

    async def process_user_message(self, message: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Processes a user's message, interacts with the LLM (potentially using tools),
        and returns the structured JSON response for the UI.
        The username is now available via self.username.
        """
        logger.info(f"User '{self.username}' message: {message}")
        
        # Construct the conversation history for the LLM
        # The system prompt is implicitly handled by the OpenAIFormClient
        # The history should just be user/assistant turns.
        # The UI_ASSISTANT_SYSTEM_PROMPT can be enhanced to use the username if desired, e.g. "You are assisting {self.username}..."
        # For now, the prompt is generic, but the service instance knows the user.

        current_conversation = history + [{"role": "user", "content": message}]

        try:
            # First call to LLM to see if it wants to use a tool or can respond directly
            llm_response_json_str = await self.llm_client.generate_json_completion_with_tools(
                system_prompt=self.system_prompt, # self.system_prompt is UI_ASSISTANT_SYSTEM_PROMPT
                conversation_history=current_conversation, # Pass the constructed history
                tools=self.tools,
                available_functions=self.available_functions,
                # Pass username to tools if they need it, e.g., for personalizing searches (not currently used by tools)
                # tool_execution_context={"username": self.username} 
            )

            # The generate_json_completion_with_tools now handles the multi-step tool calling internally.
            # The response here should be the final JSON for the UI.
            
            if not llm_response_json_str:
                logger.error(f"LLM returned empty or None response for user '{self.username}'.")
                return {"status": "error", "message": "Assistant did not provide a response."}

            try:
                response_data = json.loads(llm_response_json_str)
            except json.JSONDecodeError:
                logger.error(f"Failed to decode LLM JSON response for user '{self.username}': {llm_response_json_str}")
                # Try to give the raw output back to UI for debugging if it's not a valid JSON
                return {"status": "error", "message": "Assistant response was not valid JSON.", "raw_output": llm_response_json_str }

            # Validate the plan if one is completed
            if response_data.get("status") == "plan_complete":
                if "campaign_plan" not in response_data or not self._is_plan_valid(response_data["campaign_plan"]):
                    logger.warning(f"Generated plan for user '{self.username}' is invalid. Forcing clarification.")
                    # If plan is invalid, tell the LLM to ask for corrections
                    # This requires another call to the LLM.
                    # We can add a user message to the history indicating the validation failure.
                    current_conversation.append({"role": "assistant", "content": llm_response_json_str}) # Add AI's invalid attempt
                    current_conversation.append({"role": "user", "content": "The plan you provided is incomplete or has errors (e.g., missing phone numbers). Please review the contact details carefully and ask for any missing information."})
                    
                    # Call LLM again to get clarifying questions
                    corrected_response_str = await self.llm_client.generate_json_completion_with_tools(
                        system_prompt=self.system_prompt,
                        conversation_history=current_conversation,
                        tools=self.tools,
                        available_functions=self.available_functions
                    )
                    try:
                        response_data = json.loads(corrected_response_str)
                    except json.JSONDecodeError:
                         return {"status": "error", "message": "Assistant response after correction was not valid JSON.", "raw_output": corrected_response_str }


            logger.debug(f"Final response for user '{self.username}': {response_data}")
            return response_data

        except Exception as e:
            logger.error(f"Error processing message for user '{self.username}': {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}