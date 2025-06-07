# tools/information_retriever_svc.py

import sys
from pathlib import Path
import asyncio
import json

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

    async def search_internet(self, query: str) -> str:
        """General purpose internet search."""
        return await self.gemini_client.perform_grounded_search(query=query)

    async def get_authoritative_business_info(self, business_name: str, location_context: str) -> str:
        """
        Gets authoritative, structured information for a specific business using a 2-step process.
        First, it uses general search to find the canonical name/address, then uses the Places API for details.
        """
        logger.info(f"Getting authoritative info for '{business_name}' in '{location_context}'")

        # Step 1 (was Disambiguation, now we combine for a direct Places API query)
        # We can often directly query the Places API with a combined string.
        search_query = f"{business_name}, {location_context}"
        
        # Step 2: Precise Lookup using Places API
        # The gmaps library is synchronous, so we run it in an executor to avoid blocking asyncio
        loop = asyncio.get_running_loop()
        try:
            place_details = await loop.run_in_executor(
                None,  # Use default executor
                self.gemini_client.find_place_details,
                search_query
            )
            
            # Convert the dictionary to a JSON string to return to the LLM
            return json.dumps(place_details)
        except Exception as e:
            logger.error(f"Error running find_place_details in executor: {e}")
            return json.dumps({"error": "Failed to execute business information lookup."})