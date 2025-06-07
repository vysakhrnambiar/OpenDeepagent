# task_manager/ui_assistant_svc.py

import sys
from pathlib import Path
import json
from typing import List, Dict, Any

# ... (other imports remain the same) ...
from llm_integrations.openai_form_client import OpenAIFormClient
from tools.information_retriever_svc import InformationRetrieverService
from config.prompt_config import UI_ASSISTANT_SYSTEM_PROMPT
from common.logger_setup import setup_logger
from config.app_config import app_config

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class UIAssistantService:
    def __init__(self, openai_client: OpenAIFormClient, info_retriever: InformationRetrieverService):
        self.openai_client = openai_client
        self.info_retriever = info_retriever
        self.tool_functions = {
            "search_internet": self.info_retriever.search_internet,
            "get_authoritative_business_info": self.info_retriever.get_authoritative_business_info
        }
        self.system_prompt = UI_ASSISTANT_SYSTEM_PROMPT
    
    def _is_plan_valid(self, plan: Dict[str, Any]) -> bool:
        """
        Our new safety net. Checks if a generated plan is valid.
        Currently, it just checks for valid phone numbers.
        """
        if plan.get("status") != "plan_complete":
            return True # Not a plan, so it's valid for now.

        contacts = plan.get("campaign_plan", {}).get("contacts", [])
        if not contacts:
            logger.warning("Plan validation failed: No contacts found in the plan.")
            return False

        for contact in contacts:
            phone = contact.get("phone")
            if not phone or "undefined" in str(phone) or len(str(phone)) < 7:
                logger.error(f"Plan validation failed: Invalid phone number '{phone}' for contact '{contact.get('name')}'.")
                return False
        
        return True

    async def process_chat_interaction(self, user_id: int, conversation_history: List[Dict[str, str]]) -> Dict[str, Any]:
        logger.debug(f"Processing chat for user_id: {user_id}. History length: {len(conversation_history)}")

        # --- The entire tool-calling logic remains the same as before ---
        # ... (for brevity, I am not repeating the 60+ lines of the tool-calling logic here, 
        #      as they do not change. The logic below is what's new/important.)
        
        final_json_response = {}
        try:
            # Step 1: First call to the LLM
            # ... (code to make initial_response)
            messages_with_tools = [{"role": "system", "content": self.system_prompt}] + conversation_history
            tools = [
                {
                    "type": "function", "function": { "name": "search_internet", "description": "Performs a general internet search for any kind of information.", "parameters": { "type": "object", "properties": { "query": {"type": "string", "description": "The search query."} }, "required": ["query"], }, },
                },
                {
                    "type": "function", "function": { "name": "get_authoritative_business_info", "description": "Gets reliable, structured information (phone number, address, hours) for a specific business. Use this for stores, restaurants, offices, etc.", "parameters": { "type": "object", "properties": { "business_name": {"type": "string", "description": "The name of the business."}, "location_context": {"type": "string", "description": "The city, area, or neighborhood to search in. E.g., 'Kigali', 'downtown Toronto'"} }, "required": ["business_name", "location_context"], }, },
                }
            ]
            initial_response = await self.openai_client.async_client.chat.completions.create(
                model=self.openai_client.default_model, messages=messages_with_tools, tools=tools, tool_choice="auto", response_format={"type": "json_object"}
            )
            response_message = initial_response.choices[0].message
            tool_calls = response_message.tool_calls

            # Step 2: Tool execution
            if tool_calls:
                logger.info(f"LLM wants to call tools: {[tc.function.name for tc in tool_calls]}")
                messages_with_tools.append(response_message)
                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    function_to_call = self.tool_functions.get(function_name)
                    if function_to_call:
                        function_args = json.loads(tool_call.function.arguments)
                        function_response = await function_to_call(**function_args)
                        messages_with_tools.append({ "tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": function_response, })
                
                # Step 3: Second call
                logger.debug("Sending second request to LLM with tool results.")
                final_response_from_llm = await self.openai_client.async_client.chat.completions.create(
                    model=self.openai_client.default_model, messages=messages_with_tools, response_format={"type": "json_object"}
                )
                final_content = final_response_from_llm.choices[0].message.content
                final_json_response = json.loads(final_content)
            else:
                logger.info("LLM responded directly without calling a tool.")
                final_json_response = json.loads(response_message.content)

            # --- NEW VALIDATION STEP ---
            if not self._is_plan_valid(final_json_response):
                logger.warning("Generated plan is invalid. Forcing AI to reconsider.")
                # We tell the AI its plan was bad and ask it to fix it.
                reconsider_prompt = {
                    "role": "user",
                    "content": "The plan you just generated is invalid because it contains a missing or incorrect phone number. Please review the tool results again and either find the correct number or ask me for the information. Do not create a plan without a valid phone number for every contact."
                }
                messages_with_tools.append(reconsider_prompt)
                
                # Make one final attempt to get a good plan
                final_attempt_response = await self.openai_client.async_client.chat.completions.create(
                    model=self.openai_client.default_model, messages=messages_with_tools, response_format={"type": "json_object"}
                )
                final_json_response = json.loads(final_attempt_response.choices[0].message.content)

            return final_json_response

        except Exception as e:
            logger.error(f"Error in UIAssistantService chat processing: {e}", exc_info=True)
            return {"status": "error", "message": "I'm sorry, I encountered an internal error. Please try again."}