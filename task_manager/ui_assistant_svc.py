import sys
from pathlib import Path
import json
from typing import Dict, Any, Optional, List

# --- Path Hack for direct execution ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from llm_integrations.openai_form_client import OpenAIFormClient
from config.prompt_config import UI_ASSISTANT_SYSTEM_PROMPT
from common.logger_setup import setup_logger
from config.app_config import app_config

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class UIAssistantService:
    def __init__(self, openai_form_client: OpenAIFormClient):
        if not isinstance(openai_form_client, OpenAIFormClient):
            logger.error("openai_form_client provided to UIAssistantService is not an instance of OpenAIFormClient.")
            raise TypeError("openai_form_client must be an instance of OpenAIFormClient")
        self.openai_form_client = openai_form_client

    async def get_next_chat_response(
        self,
        chat_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Takes the current chat history, gets the next response from the LLM,
        and returns it as a structured dictionary.
        
        Args:
            chat_history: A list of messages, e.g., [{"role": "user", "content": "..."}]

        Returns:
            A dictionary representing the LLM's structured JSON response.
        """
        if not chat_history:
            logger.warning("get_next_chat_response called with empty chat_history.")
            return {"status": "error", "message": "Chat history cannot be empty."}

        # We don't need to build a complex user prompt string here.
        # We'll pass the whole history to the LLM, which is better for context.
        # The user's latest message should be the last one in the history.
        logger.info(f"Processing chat history. Last user message: '{chat_history[-1]['content'][:100]}...'")

        # The OpenAIFormClient's generate_json_completion is perfect for this.
        # It handles the system prompt and expects a user prompt (or history).
        # We can construct a simple string for the user_prompt arg or adapt the client.
        # Let's adapt by passing the history directly.

        try:
            # We'll create the message list to be sent to the API
            messages_for_api = [
                {"role": "system", "content": UI_ASSISTANT_SYSTEM_PROMPT}
            ]
            messages_for_api.extend(chat_history)

            logger.debug(f"Sending to LLM with {len(messages_for_api)} total messages.")
            
            # Use the async_client directly for more control over messages
            response = await self.openai_form_client.async_client.chat.completions.create(
                model=self.openai_form_client.default_model,
                messages=messages_for_api,
                temperature=0.2, # Lower temperature for more predictable JSON
                response_format={"type": "json_object"}
            )
            
            content_str = response.choices[0].message.content
            if not content_str:
                logger.warning("LLM returned empty content for JSON request.")
                return {"status": "error", "message": "LLM returned an empty response."}

            logger.debug(f"Received raw JSON from LLM: {content_str[:300]}...")
            
            # The JSON parsing logic from our client can be used here
            parsed_json = json.loads(content_str)
            
            # Basic validation of the returned structure
            valid_stati = ["needs_more_info", "plan_complete", "clarifying"]
            if "status" not in parsed_json or parsed_json["status"] not in valid_stati:
                 raise ValueError("LLM response missing or has invalid 'status'.")

            return parsed_json

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM response: {e}. Raw content: {content_str[:500]}")
            return {"status": "error", "message": "LLM returned invalid JSON."}
        except Exception as e:
            logger.error(f"An unexpected error occurred in UIAssistantService: {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected server error occurred: {e}"}


# --- Test Block ---
async def main_test_ui_assistant_svc():
    if not app_config.OPENAI_API_KEY:
        print("Skipping UIAssistantService test: OPENAI_API_KEY not set.")
        return

    print("--- Testing UIAssistantService ---")
    form_client = OpenAIFormClient()
    ui_assistant = UIAssistantService(openai_form_client=form_client)

    # Test Case 1: Vague initial goal
    print("\n--- Test Case 1: Vague initial goal ---")
    history_1 = [
        {"role": "user", "content": "I want to invite my friends to a party."}
    ]
    print(f"User > {history_1[0]['content']}")
    response_1 = await ui_assistant.get_next_chat_response(history_1)
    print(f"Assistant >\n{json.dumps(response_1, indent=2)}")
    if response_1.get("status") == "needs_more_info":
        print("\nSUCCESS: Assistant correctly asked for more information.")
    else:
        print("\nFAILURE: Assistant did not ask for information as expected.")


    # Test Case 2: Providing answers to the questions
    print("\n--- Test Case 2: User provides answers ---")
    # Simulate the history after user answers the assistant's first questions
    answers_from_user = """
    The friends are:
    - Jhon 1: +919744554079
    - Jhon 2: +919744554080
    - Jhon 3: +919744554081

    The party is on June 16th at 6 PM at my house in Kigali.
    The dress code is white and white.
    """
    history_2 = [
        {"role": "user", "content": "I want to invite my friends to a party."},
        {"role": "assistant", "content": json.dumps(response_1)}, # The assistant's last turn
        {"role": "user", "content": f"Here are the answers: {answers_from_user}"} # The user's new answers
    ]
    print(f"User > (Provides details for the party...)")
    response_2 = await ui_assistant.get_next_chat_response(history_2)
    print(f"Assistant >\n{json.dumps(response_2, indent=2)}")
    if response_2.get("status") == "plan_complete":
        print("\nSUCCESS: Assistant correctly generated the final campaign plan.")
    elif response_2.get("status") == "needs_more_info":
        print("\nINFO: Assistant is asking for more refinement, which is also a valid state.")
    else:
        print("\nFAILURE: Assistant failed to generate the final plan.")

    await form_client.close_client()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main_test_ui_assistant_svc())