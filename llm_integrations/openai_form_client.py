import sys
from pathlib import Path
import asyncio

# --- Path Hack for direct execution ---
_project_root_for_direct_execution = Path(__file__).resolve().parent.parent
if str(_project_root_for_direct_execution) not in sys.path:
    sys.path.insert(0, str(_project_root_for_direct_execution))
# --- End Path Hack ---

import openai # Main openai library
# For async, we need AsyncOpenAI
from openai import AsyncOpenAI # <--- IMPORT AsyncOpenAI
import json
from typing import Dict, Any, Optional, List

from config.app_config import app_config
from config.prompt_config import (
    AGENT_INSTRUCTION_GENERATOR_SYSTEM_PROMPT,
    POST_CALL_ANALYSIS_SYSTEM_PROMPT
)
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)


class OpenAIFormClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or app_config.OPENAI_API_KEY
        if not self.api_key:
            logger.error("OpenAI API key is not configured for OpenAIFormClient.")
            raise ValueError("OpenAI API key must be provided or set in app_config.")
        try:
            # Use AsyncOpenAI for async methods
            self.async_client = AsyncOpenAI(api_key=self.api_key) # <--- USE AsyncOpenAI
        except Exception as e:
            logger.error(f"Failed to initialize AsyncOpenAI client: {e}")
            raise
        self.default_model = app_config.OPENAI_FORM_LLM_MODEL

    async def generate_text_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 1024
    ) -> Optional[str]:
        current_model = model or self.default_model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        try:
            logger.debug(f"Sending request to OpenAI model {current_model} with system prompt: {system_prompt[:100]}... user_prompt: {user_prompt[:100]}...")
            # Use the async_client
            response = await self.async_client.chat.completions.create( # <--- USE self.async_client
                model=current_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = response.choices[0].message.content
            logger.debug(f"Received response from OpenAI: {content[:100] if content else 'None'}...")
            return content.strip() if content else None
        except openai.APIConnectionError as e:
            logger.error(f"OpenAI API connection error: {e}")
        except openai.RateLimitError as e:
            logger.error(f"OpenAI API request exceeded rate limit: {e}")
        except openai.APIStatusError as e:
            logger.error(f"OpenAI API returned an API Status Error: Status {e.status_code}, Response: {e.response}")
        except Exception as e:
            logger.error(f"An unexpected error occurred with OpenAI API: {e}")
        return None

    async def generate_json_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024
    ) -> Optional[Dict[str, Any]]:
        current_model = model or self.default_model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        try:
            logger.debug(f"Sending request for JSON to OpenAI model {current_model}. User prompt: {user_prompt[:100]}...")
            # Use the async_client
            response = await self.async_client.chat.completions.create( # <--- USE self.async_client
                model=current_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )
            content_str = response.choices[0].message.content
            if not content_str:
                logger.warning("OpenAI returned empty content for JSON request.")
                return None

            logger.debug(f"Received raw JSON string from OpenAI: {content_str[:200]}...")
            json_start = content_str.find('{')
            json_end = content_str.rfind('}')
            if json_start != -1 and json_end != -1 and json_end > json_start:
                json_str_cleaned = content_str[json_start : json_end + 1]
                try:
                    parsed_json = json.loads(json_str_cleaned)
                    logger.debug(f"Successfully parsed JSON response.")
                    return parsed_json
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON from OpenAI response: {e}. Raw content excerpt: {content_str[:500]}")
                    return None
            else:
                logger.warning(f"Could not find valid JSON structure in response: {content_str[:500]}")
                return None
        except openai.APIConnectionError as e:
            logger.error(f"OpenAI API connection error (JSON mode): {e}")
        except openai.RateLimitError as e:
            logger.error(f"OpenAI API request exceeded rate limit (JSON mode): {e}")
        except openai.APIStatusError as e:
            logger.error(f"OpenAI API returned an API Status Error (JSON mode): Status {e.status_code}, Response: {e.response}")
        except Exception as e:
            logger.error(f"An unexpected error occurred with OpenAI API (JSON mode): {e}")
        return None

    # Add a method to properly close the async client if needed during app shutdown
    async def close_client(self):
        if hasattr(self, 'async_client') and self.async_client:
            await self.async_client.close()
            logger.info("AsyncOpenAI client closed.")


async def main_test():
    if not app_config.OPENAI_API_KEY:
        print("Skipping OpenAIFormClient test: OPENAI_API_KEY not set.")
        logger.warning("Skipping OpenAIFormClient test: OPENAI_API_KEY not set.")
        return

    print("Testing OpenAIFormClient...")
    logger.info("Starting OpenAIFormClient test...")
    form_client = None # Initialize to None
    try:
        form_client = OpenAIFormClient()
    except ValueError as e:
        print(f"Failed to initialize OpenAIFormClient: {e}")
        logger.error(f"Failed to initialize OpenAIFormClient: {e}")
        return
    except Exception as e_init: # Catch any other init errors
        print(f"Unexpected error initializing OpenAIFormClient: {e_init}")
        logger.error(f"Unexpected error initializing OpenAIFormClient: {e_init}")
        return


    print("\n--- Test 1: Text Generation ---")
    logger.info("--- Test 1: Text Generation ---")
    user_task_desc = "I need an AI agent to call a customer and confirm their delivery address for order #12345."
    simple_system_prompt_text_gen = "You are an assistant that creates concise instructions for an AI call agent."
    generated_instructions = await form_client.generate_text_completion(
        system_prompt=simple_system_prompt_text_gen,
        user_prompt=f"Generate instructions for this task: {user_task_desc}"
    )
    if generated_instructions:
        print(f"Generated Instructions (Text):\n{generated_instructions}")
        logger.info(f"Generated Text Instructions successfully.")
    else:
        print("Failed to generate text instructions.")
        logger.error("Failed to generate text instructions.")

    print("\n--- Test 2: JSON Generation ---")
    logger.info("--- Test 2: JSON Generation ---")
    simple_system_prompt_json_gen = POST_CALL_ANALYSIS_SYSTEM_PROMPT
    mock_task_prompt = "Your goal was to schedule a demo for Product X."
    mock_transcript = "Agent: Hello, I'm calling about Product X. User: I'm interested. Agent: Great, is Tuesday good? User: Yes. Agent: Confirmed for Tuesday."
    json_analysis_user_prompt = f"""
Original Task Instructions: {mock_task_prompt}
Call Transcript:
{mock_transcript}

Please analyze and provide the JSON output as per the system instructions.
"""
    analysis_result = await form_client.generate_json_completion(
        system_prompt=simple_system_prompt_json_gen,
        user_prompt=json_analysis_user_prompt
    )
    if analysis_result:
        print(f"Analysis Result (JSON):\n{json.dumps(analysis_result, indent=2)}")
        logger.info(f"Generated JSON Analysis successfully.")
        if isinstance(analysis_result, dict) and analysis_result.get("task_completed") is True:
            print("JSON structure seems as expected for a completed task.")
            logger.info("JSON structure for completed task seems as expected.")
    else:
        print("Failed to get JSON analysis.")
        logger.error("Failed to get JSON analysis.")

    # Clean up the client
    if form_client:
        await form_client.close_client()

if __name__ == "__main__":
    asyncio.run(main_test())