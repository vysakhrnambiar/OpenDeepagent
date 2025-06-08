import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional # Added Optional
import openai # Ensure openai is imported
from openai import AsyncOpenAI # Ensure AsyncOpenAI is imported for async operations
import asyncio
# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__)

class OpenAIFormClient:
    def __init__(self):
        if not app_config.OPENAI_API_KEY:
            logger.error("OpenAI API key is not configured.")
            raise ValueError("OpenAI API key is missing.")
        
        # Use AsyncOpenAI for async methods
        self.async_client = AsyncOpenAI(api_key=app_config.OPENAI_API_KEY)
        self.model = app_config.OPENAI_FORM_LLM_MODEL

    async def generate_json_completion(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """
        Generates a completion from OpenAI expecting a JSON string as output.
        This is a simpler version without tool/function calling.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        try:
            logger.debug(f"Sending request to OpenAI. Model: {self.model}. System Prompt: '{system_prompt[:100]}...'. User Prompt: '{user_prompt[:100]}...'")
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"} # Request JSON output
            )
            content = response.choices[0].message.content
            logger.debug(f"Received OpenAI JSON response: {content[:200]}...")
            return content
        except openai.APIError as e: # Catch specific OpenAI errors
            logger.error(f"OpenAI API error during JSON completion: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Unexpected error during OpenAI JSON completion: {e}", exc_info=True)
            return None

    # --- NEW METHOD TO BE ADDED ---
    async def generate_json_completion_with_tools(
        self,
        system_prompt: str,
        conversation_history: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        available_functions: Dict[str, Callable],
        max_tool_iterations: int = 3 # Prevent infinite loops
    ) -> Optional[str]:
        """
        Generates a completion from OpenAI, supporting multi-step tool calls,
        and expects a final JSON string response from the LLM (not from a tool).
        """
        messages = [{"role": "system", "content": system_prompt}] + conversation_history
        
        for _ in range(max_tool_iterations):
            logger.debug(f"OpenAI request (iteration {_ + 1}): Model={self.model}, Messages Snapshot (last 2): {messages[-2:]}, Tools: {'Yes' if tools else 'No'}")
            
            try:
                response = await self.async_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools if tools else None, # Pass tools only if they exist
                    tool_choice="auto" if tools else None, # Let OpenAI decide if it needs to call a tool
                    response_format={"type": "json_object"} # Still expect final LLM response as JSON
                )
            except openai.APIError as e:
                logger.error(f"OpenAI API error during tool completion: {e}", exc_info=True)
                return json.dumps({"status": "error", "message": f"OpenAI API Error: {str(e)}"})
            except Exception as e:
                logger.error(f"Unexpected error calling OpenAI: {e}", exc_info=True)
                return json.dumps({"status": "error", "message": f"Unexpected Error: {str(e)}"})

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if not tool_calls:
                # No tool calls, LLM has responded directly with final JSON content
                logger.debug(f"OpenAI response (no tool call): {response_message.content[:200]}...")
                if response_message.content:
                    return response_message.content # This should be the final JSON from the LLM
                else:
                    logger.warning("LLM responded with no tool calls and no content.")
                    return json.dumps({"status": "error", "message": "Assistant provided no content."})

            # If there are tool calls, process them
            messages.append(response_message) # Add assistant's message with tool calls to history

            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_to_call = available_functions.get(function_name)
                
                if not function_to_call:
                    logger.error(f"Function '{function_name}' is not available.")
                    # Append an error message for the LLM to see
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps({"error": f"Tool '{function_name}' not found."})
                    })
                    continue # Continue to next tool call or next iteration

                try:
                    function_args = json.loads(tool_call.function.arguments)
                    logger.info(f"Calling tool: {function_name} with args: {function_args}")
                    
                    # Check if the function is async
                    if asyncio.iscoroutinefunction(function_to_call):
                        function_response = await function_to_call(**function_args)
                    else:
                        function_response = function_to_call(**function_args)

                    logger.info(f"Tool {function_name} response: {str(function_response)[:200]}...")
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(function_response), # Ensure response is string; tools should return JSON strings or simple strings
                    })
                except Exception as e:
                    logger.error(f"Error executing tool {function_name}: {e}", exc_info=True)
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps({"error": f"Error executing tool '{function_name}': {str(e)}"})
                    })
        
        logger.warning(f"Exceeded max tool iterations ({max_tool_iterations}). Returning last known assistant message or error.")
        # If loop finishes, it means max_tool_iterations was hit without a direct LLM response.
        # Try to return the last message if it was from assistant, otherwise an error.
        if messages and messages[-1]["role"] == "assistant" and messages[-1].get("content"):
            return messages[-1]["content"]
        return json.dumps({"status": "error", "message": "Max tool iterations reached. Assistant did not provide a final response."})


if __name__ == '__main__':
    # Example usage (requires async environment to run)
    async def run_test():
        client = OpenAIFormClient()
        
        # Test 1: Simple JSON completion (no tools)
        # print("\n--- Test 1: Simple JSON Completion ---")
        # system_prompt_simple = "You are a helpful assistant that provides structured data. Respond with a JSON object containing a 'greeting' and a 'subject'."
        # user_prompt_simple = "Tell me about today."
        # response_simple = await client.generate_json_completion(system_prompt_simple, user_prompt_simple)
        # print(f"Simple JSON Response:\n{response_simple}")

        # Test 2: Completion with tools
        print("\n--- Test 2: JSON Completion with Tools ---")
        def get_current_weather(location: str, unit: str = "celsius"):
            """Get the current weather in a given location"""
            # This is a mock function
            if "tokyo" in location.lower():
                return json.dumps({"location": "Tokyo", "temperature": "10", "unit": unit})
            elif "san francisco" in location.lower():
                return json.dumps({"location": "San Francisco", "temperature": "72", "unit": "fahrenheit"})
            else:
                return json.dumps({"location": location, "temperature": "unknown"})

        system_prompt_tools = "You are a helpful assistant. Respond in JSON. Use tools if necessary."
        conversation_history_tools = [
            {"role": "user", "content": "What's the weather like in San Francisco?"}
        ]
        tools_def = [
            {
                "type": "function",
                "function": {
                    "name": "get_current_weather",
                    "description": "Get the current weather in a given location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {
                                "type": "string",
                                "description": "The city and state, e.g. San Francisco, CA",
                            },
                            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                        },
                        "required": ["location"],
                    },
                },
            }
        ]
        available_funcs = {"get_current_weather": get_current_weather}

        response_with_tools = await client.generate_json_completion_with_tools(
            system_prompt=system_prompt_tools,
            conversation_history=conversation_history_tools,
            tools=tools_def,
            available_functions=available_funcs
        )
        print(f"Response with Tools:\n{response_with_tools}")

    import asyncio
    asyncio.run(run_test())