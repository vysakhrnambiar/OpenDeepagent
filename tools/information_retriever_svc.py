import sys
from pathlib import Path

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from llm_integrations.google_gemini_client import GoogleGeminiClient
from common.logger_setup import setup_logger
from config.app_config import app_config

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class InformationRetrieverService:
    def __init__(self, gemini_client: GoogleGeminiClient):
        self.gemini_client = gemini_client
        logger.info("InformationRetrieverService initialized.")

    async def search_internet(self, query: str) -> str:
        """
        This is the actual Python function that gets executed. 
        It takes a search query string and uses the Gemini client to find the answer.
        """
        logger.info(f"Executing tool 'search_internet' with query: '{query}'")
        
        # The query is already formulated by the primary LLM, so we just execute it.
        search_results = await self.gemini_client.perform_grounded_search(query)
        
        return search_results