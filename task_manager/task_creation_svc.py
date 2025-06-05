import sys # Standard library
from pathlib import Path # Standard library
import asyncio # Standard library (used in test block)

# --- Path Hack for direct execution ---
# This must be at the TOP of the script for module-level imports to work.
_project_root_for_direct_execution = Path(__file__).resolve().parent.parent
if str(_project_root_for_direct_execution) not in sys.path:
    sys.path.insert(0, str(_project_root_for_direct_execution))
# --- End Path Hack ---

from typing import Dict, Any, Optional, List, Union # Standard library
import json # Standard library

# Now, these project-level imports should work when script is run directly
#from database.db_manager import DbManager # We won't use db_manager in this service directly yet, but import is fine
from llm_integrations.openai_form_client import OpenAIFormClient
from config.prompt_config import (
    AGENT_INSTRUCTION_GENERATOR_SYSTEM_PROMPT,
    INFORMATION_GATHERING_HELPER_SYSTEM_PROMPT
)
from common.logger_setup import setup_logger # This should be found now
from config.app_config import app_config # This should be found now

# Initialize logger after paths are set and config can be imported
logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class TaskCreationService:
    def __init__(self, openai_form_client: OpenAIFormClient):
        if not isinstance(openai_form_client, OpenAIFormClient):
            logger.error("openai_form_client provided to TaskCreationService is not an instance of OpenAIFormClient.")
            raise TypeError("openai_form_client must be an instance of OpenAIFormClient")
        self.openai_form_client = openai_form_client

    async def _ask_llm_for_agent_prompt(self, user_task_description: str, existing_details: Optional[Dict[str, Any]] = None) -> Optional[str]:
        prompt_for_llm = f"The user wants to create an AI phone agent for the following task:\n\"{user_task_description}\"\n"
        if existing_details:
            prompt_for_llm += "\nThey have already provided these details:\n"
            for key, value in existing_details.items():
                if value:
                    prompt_for_llm += f"- {key.replace('_', ' ').capitalize()}: {value}\n"
        
        prompt_for_llm += "\nPlease analyze and respond according to the system instructions, using either '[QUESTIONS_FOR_USER]' or '[AGENT_INSTRUCTIONS]' marker."
       
        llm_response_content = await self.openai_form_client.generate_text_completion(
            system_prompt=AGENT_INSTRUCTION_GENERATOR_SYSTEM_PROMPT,
            user_prompt=prompt_for_llm,
            temperature=0.3
        )
        return llm_response_content

    async def process_user_task_and_generate_prompt(
        self,
        user_task_description: str,
        current_details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if not user_task_description:
            logger.warning("User task description is empty.")
            return {"status": "error", "message": "User task description cannot be empty."}

        current_details = current_details or {}
        logger.info(f"Processing user task: '{user_task_description[:100]}...' with current details: {current_details}")

        llm_output = await self._ask_llm_for_agent_prompt(user_task_description, current_details)

        if not llm_output:
            logger.error("LLM failed to generate a response for prompt creation.")
            return {"status": "error", "message": "Failed to get response from LLM for prompt generation."}

        llm_output_stripped = llm_output.strip()

        if llm_output_stripped.startswith("[QUESTIONS_FOR_USER]"):
            questions = llm_output_stripped.replace("[QUESTIONS_FOR_USER]", "", 1).strip()
            logger.info(f"LLM response is questions for the user: {questions[:200]}...")
            return {
                "status": "needs_more_info",
                "questions_for_user": questions,
                "current_agent_prompt_suggestion": None # No prompt suggestion if asking questions
            }
        elif llm_output_stripped.startswith("[AGENT_INSTRUCTIONS]"):
            agent_prompt = llm_output_stripped.replace("[AGENT_INSTRUCTIONS]", "", 1).strip()
            logger.info(f"LLM generated agent prompt: {agent_prompt[:100]}...")
            # Basic validation: ensure prompt is not empty after stripping marker
            if not agent_prompt:
                logger.warning("LLM provided [AGENT_INSTRUCTIONS] marker but the prompt content was empty.")
                return {"status": "error", "message": "LLM indicated agent instructions but content was empty."}
            return {
                "status": "prompt_generated",
                "agent_prompt": agent_prompt,
                "questions_for_user": None
            }
        else:
            # Fallback: LLM didn't use the expected markers
            logger.warning(f"LLM output did not start with expected marker '[QUESTIONS_FOR_USER]' or '[AGENT_INSTRUCTIONS]'. Output: {llm_output[:300]}...")
            # We can either return an error, or try to infer, or just pass it through and let UI decide.
            # For robustness, let's assume if no marker, it might be a malformed prompt or general chat.
            # Let's treat it as potentially needing more info or being an unconfirmed prompt.
            # For now, returning an error is safer to highlight the LLM isn't following instructions.
            return {
                "status": "error",
                "message": "LLM output format was unexpected (missing required markers). Please try rephrasing your task or check LLM.",
                "raw_llm_output": llm_output # Include raw output for debugging
            }

async def main_test_task_creation_svc():
    if not app_config.OPENAI_API_KEY: # app_config should be available now
        print("Skipping TaskCreationService test: OPENAI_API_KEY not set.")
        logger.warning("Skipping TaskCreationService test: OPENAI_API_KEY not set.")
        return

    print("Testing TaskCreationService...")
    logger.info("Starting TaskCreationService test...")

    form_client = None
    try:
        form_client = OpenAIFormClient()
    except Exception as e:
        print(f"Failed to initialize OpenAIFormClient for test: {e}")
        logger.error(f"Failed to initialize OpenAIFormClient for test: {e}")
        return

    task_service = TaskCreationService(openai_form_client=form_client)

    print("\n--- Test Case 1: Vague user task ---")
    user_input_1 = "I want to call someone about an order."
    print(f"User input: {user_input_1}")
    result_1 = await task_service.process_user_task_and_generate_prompt(user_input_1)
    print(f"Service Result 1:\n{json.dumps(result_1, indent=2)}")
    if result_1["status"] == "needs_more_info":
        print("SUCCESS: Service correctly identified need for more info.")
        logger.info("Test Case 1: Vague task - SUCCESS, needs more info.")
    else:
        print("FAILURE: Service did not ask for more info for a vague task as expected based on heuristic.")
        logger.error(f"Test Case 1: Vague task - UNEXPECTED (status: {result_1['status']}). Prompt: {result_1.get('agent_prompt', '')[:200]}")


    print("\n--- Test Case 2: User provides some details ---")
    user_input_2 = "Call customer Jane Doe about order #ABC112233 to confirm shipping address."
    current_details_2 = {"customer_name": "Jane Doe", "order_number": "ABC112233", "purpose": "confirm shipping address"}
    print(f"User input (effectively via UI): {user_input_2}")
    print(f"Existing details (collected by UI): {current_details_2}")
    result_2 = await task_service.process_user_task_and_generate_prompt(user_input_2, current_details=current_details_2)
    print(f"Service Result 2:\n{json.dumps(result_2, indent=2)}")
    if result_2["status"] == "prompt_generated" and result_2.get("agent_prompt"):
        print("SUCCESS: Service generated an agent prompt.")
        logger.info("Test Case 2: Detailed task - SUCCESS, prompt generated.")
    elif result_2["status"] == "needs_more_info":
        print("INFO: Service still thinks it needs more info, or the heuristic to differentiate is tricky.")
        logger.info(f"Test Case 2: Detailed task - INFO, LLM asked for more. Questions: {result_2.get('questions_for_user', '')[:200]}")
    else:
        print("FAILURE: Service failed to process detailed task.")
        logger.error("Test Case 2: Detailed task - FAILURE.")

    if form_client:
        await form_client.close_client()
    logger.info("TaskCreationService test finished.")


if __name__ == "__main__":
    # The path hack is now at the top of the file.
    asyncio.run(main_test_task_creation_svc())