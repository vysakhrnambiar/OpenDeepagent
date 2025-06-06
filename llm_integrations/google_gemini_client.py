import sys
from pathlib import Path

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

import google.generativeai as genai
from google.generativeai.types import grounding

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class GoogleGeminiClient:
    def __init__(self):
        self.api_key = app_config.GOOGLE_API_KEY
        if not self.api_key:
            logger.error("Google API key is not configured.")
            raise ValueError("Google API key must be set in app_config.")
        
        genai.configure(api_key=self.api_key)
        self.search_tool = grounding.Tool(
            google_search=grounding.GoogleSearch()
        )
        self.model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            tools=[self.search_tool]
        )
        logger.info("GoogleGeminiClient initialized with search grounding tool.")

    async def perform_grounded_search(self, query: str) -> str:
        """
        Performs a search-grounded query using Gemini.
        """
        logger.info(f"Performing grounded search with query: '{query}'")
        try:
            response = await self.model.generate_content_async(query)
            return response.text
        except Exception as e:
            logger.error(f"An unexpected error occurred with Google Gemini API: {e}", exc_info=True)
            return f"Error: Could not perform search due to an internal error: {e}"