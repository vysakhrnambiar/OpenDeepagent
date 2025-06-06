import sys
from pathlib import Path
import asyncio

# --- Path Hack for direct execution ---
_project_root_for_direct_execution = Path(__file__).resolve().parent.parent
if str(_project_root_for_direct_execution) not in sys.path:
    sys.path.insert(0, str(_project_root_for_direct_execution))
# --- End Path Hack ---

import openai
from openai import AsyncOpenAI
import json
from typing import Dict, Any, Optional, List

from config.app_config import app_config
# ***** PROMPT IMPORT REMOVED - THIS IS THE FIX *****
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)


class OpenAIFormClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or app_config.OPENAI_API_KEY
        if not self.api_key:
            logger.error("OpenAI API key is not configured for OpenAIFormClient.")
            raise ValueError("OpenAI API key must be provided or set in app_config.")
        try:
            self.async_client = AsyncOpenAI(api_key=self.api_key)
        except Exception as e:
            logger.error(f"Failed to initialize AsyncOpenAI client: {e}")
            raise
        self.default_model = app_config.OPENAI_FORM_LLM_MODEL

    # The methods generate_text_completion and generate_json_completion
    # are already designed correctly: they accept a system_prompt as an argument.
    # No changes are needed to them. I'm including them for completeness.

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
            logger.debug(f"Sending request to OpenAI model {current_model}...")
            response = await self.async_client.chat.completions.create(
                model=current_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception as e:
            logger.error(f"An unexpected error occurred with OpenAI API: {e}")
        return None

    async def generate_json_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048
    ) -> Optional[Dict[str, Any]]:
        current_model = model or self.default_model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        try:
            logger.debug(f"Sending request for JSON to OpenAI model {current_model}...")
            response = await self.async_client.chat.completions.create(
                model=current_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )
            content_str = response.choices[0].message.content
            if not content_str:
                return None
            return json.loads(content_str)
        except Exception as e:
            logger.error(f"An unexpected error occurred with OpenAI API (JSON mode): {e}")
        return None

    async def close_client(self):
        if hasattr(self, 'async_client') and self.async_client:
            await self.async_client.close()
            logger.info("AsyncOpenAI client closed.")


# The test block for this file will now fail if run directly, because it needs a prompt.
# That is OK. The real test is now in the service that uses this client.
if __name__ == "__main__":
    print("This file is intended to be used as a module and not run directly.")
    print("The main test logic is now in the service files that use this client,")
    print("e.g., 'task_manager/ui_assistant_svc.py'.")