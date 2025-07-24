# llm_integrations/google_gemini_client.py

import sys
from pathlib import Path
import requests
import json
import asyncio

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from google import genai
# Import the specific types needed to construct the tool correctly
from google.genai.types import Tool, GenerateContentConfig, GoogleSearchRetrieval

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class GoogleGeminiClient:
    def __init__(self):
        self.api_key = app_config.GOOGLE_API_KEY
        if not self.api_key:
            logger.error("Google API key is not configured.")
            raise ValueError("Google API key must be set in app_config.")
        
        self.client = genai.Client(api_key=self.api_key)
        logger.info("GoogleGeminiClient initialized with google.genai.Client.")

    def _perform_grounded_search_sync(self, query: str) -> str:
        """
        Synchronous implementation of the grounded search, corrected based on API feedback.
        """
        try:
            # The API requires the `google_search_retrieval` field,
            # instantiated with the GoogleSearchRetrieval object.
            grounding_tool = Tool(
                google_search_retrieval=GoogleSearchRetrieval()
            )
            config = GenerateContentConfig(
                tools=[grounding_tool]
            )
            response = self.client.models.generate_content(
                model="gemini-1.5-flash",
                contents=query,
                config=config,
            )
            logger.info("Successfully received response from sync Google search.")
            return response.text or "" # Ensure we always return a string
        except Exception as e:
            logger.error(f"ERROR in sync Google search: {e}", exc_info=True)
            return f"Error: Could not get search results from Google AI. Detail: {str(e)}"

    async def perform_grounded_search(self, query: str) -> str:
        """
        Asynchronous wrapper for the grounded search.
        """
        logger.info(f"Performing grounded search for query: '{query}'")
        try:
            return await asyncio.to_thread(self._perform_grounded_search_sync, query)
        except Exception as e:
            logger.error(f"An unexpected error occurred in async wrapper for grounded search: {e}", exc_info=True)
            return f"Error: Could not perform search due to an internal error: {e}"

    def find_place_details(self, query: str) -> dict:
        """
        Uses the MODERN "Places API (New)" to find details for a specific place.
        """
        logger.info(f"Finding place details with Places API (New) for query: '{query}'")
        url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.internationalPhoneNumber,places.websiteUri,places.regularOpeningHours"
        }
        data = {"textQuery": query}
        try:
            response = requests.post(url, headers=headers, data=json.dumps(data))
            response.raise_for_status()
            response_data = response.json()
            logger.debug(f"Places API (New) raw response: {response_data}")
            places = response_data.get('places')
            if not places:
                logger.warning(f"Places API (New) returned no places for query: '{query}'")
                return {"error": "No business found matching that name and location."}
            place = places[0]
            authoritative_info = {
                "name": place.get("displayName", {}).get("text", "N/A"),
                "formatted_address": place.get("formattedAddress", "N/A"),
                "international_phone_number": place.get("internationalPhoneNumber", "N/A"),
                "opening_hours": {"weekday_text": place.get("regularOpeningHours", {}).get("weekdayDescriptions", [])},
                "website": place.get("websiteUri", "N/A")
            }
            return authoritative_info
        except requests.exceptions.HTTPError as e:
            error_details = e.response.json()
            logger.error(f"HTTP Error from Places API (New): {e.response.status_code} - {error_details}", exc_info=True)
            return {"error": f"Google Places service returned an error: {error_details.get('error', {}).get('message', 'Unknown error')}"}
        except Exception as e:
            logger.error(f"An unexpected error occurred calling Places API (New): {e}", exc_info=True)
            return {"error": f"An internal error occurred while contacting the places service: {e}"}