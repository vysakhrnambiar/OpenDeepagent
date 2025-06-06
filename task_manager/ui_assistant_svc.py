import sys
from pathlib import Path
import json
from typing import Dict, Any, Optional, List

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from llm_integrations.openai_form_client import OpenAIFormClient
from tools.information_retriever_svc import InformationRetrieverService
from config.prompt_config import UI_ASSISTANT_SYSTEM_PROMPT
from common.logger_setup import setup_logger
from config.app_config import app_config

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class UIAssistantService:
    def __init__(self, openai_form_client: OpenAIFormClient, retriever_service: InformationRetrieverService):
        self.openai_form_client = openai_form_client
        self.retriever_service = retriever_service
        
        # Define the NEW generic tool structure for OpenAI
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_internet",
                    "description": "Searches the internet for factual, up-to-date information on any topic, such as businesses, products, or general knowledge.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "A clear and specific search query. For example: 'hospitals in Kacyiru Kigali phone numbers' or 'best budget smartphones 2024'.",
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        ]
        
        # Map the NEW tool name to the updated callable method
        self.available_tools = {
            "search_internet": self.retriever_service.search_internet,
        }

    async def get_next_chat_response(self, chat_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not chat_history:
            return {"status": "error", "message": "Chat history cannot be empty."}

        messages_for_api = [{"role": "system", "content": UI_ASSISTANT_SYSTEM_PROMPT}]
        messages_for_api.extend(chat_history)

        try:
            # First API call to see if a tool is needed
            logger.debug("Making initial call to OpenAI to check for tool use...")
            response = await self.openai_form_client.async_client.chat.completions.create(
                model=self.openai_form_client.default_model,
                messages=messages_for_api,
                tools=self.tools,
                tool_choice="auto",
            )
            response_message = response.choices[0].message

            # Check if the model wants to call a tool
            tool_calls = response_message.tool_calls
            if tool_calls:
                logger.info(f"OpenAI model requested to use a tool: {tool_calls[0].function.name}")
                messages_for_api.append(response_message) # Append assistant's reply
                
                tool_call = tool_calls[0]
                function_name = tool_call.function.name
                function_to_call = self.available_tools[function_name]
                function_args = json.loads(tool_call.function.arguments)
                
                # Execute the tool
                function_response = await function_to_call(**function_args)
                
                # Append the tool's result to the conversation
                messages_for_api.append(
                    {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response,
                    }
                )
                
                # Second API call to get the final user-facing response
                logger.debug("Making second call to OpenAI with tool results...")
                second_response = await self.openai_form_client.async_client.chat.completions.create(
                    model=self.openai_form_client.default_model,
                    messages=messages_for_api,
                    response_format={"type": "json_object"}
                )
                content_str = second_response.choices[0].message.content
            
            else:
                # No tool call needed, it's a direct JSON response
                logger.info("OpenAI responded directly without tool use.")
                json_response = await self.openai_form_client.async_client.chat.completions.create(
                    model=self.openai_form_client.default_model,
                    messages=messages_for_api,
                    response_format={"type": "json_object"}
                )
                content_str = json_response.choices[0].message.content

            if not content_str:
                return {"status": "error", "message": "LLM returned an empty response."}
            
            return json.loads(content_str)

        except Exception as e:
            logger.error(f"An error occurred in UIAssistantService: {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected server error occurred."}