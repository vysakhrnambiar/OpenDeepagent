# llm_integrations/google_gemini_client.py

import sys
from pathlib import Path
import requests  # New import for making HTTP requests
import json      # To construct the request body

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

import google.generativeai as genai
from google.generativeai.types import Tool 

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
        
        # We no longer need the gmaps_client
        self.generative_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash'
        )
        logger.info("GoogleGeminiClient initialized with generative model.")

    async def perform_grounded_search(self, query: str) -> str:
        # This method remains unchanged
        logger.info(f"Performing general grounded search with query: '{query}'")
        search_tool = [Tool(google_search_retrieval={})]
        try:
            response = await self.generative_model.generate_content_async(
                query,
                tools=search_tool
            )
            return response.text
        except Exception as e:
            logger.error(f"An unexpected error occurred with Gemini grounded search: {e}", exc_info=True)
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
            # This specifies which fields we want back. It's more efficient.
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.internationalPhoneNumber,places.websiteUri,places.regularOpeningHours"
        }
        
        data = {
            "textQuery": query
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(data))
            response.raise_for_status()  # This will raise an exception for bad status codes (4xx or 5xx)
            
            response_data = response.json()
            logger.debug(f"Places API (New) raw response: {response_data}")

            places = response_data.get('places')
            if not places:
                logger.warning(f"Places API (New) returned no places for query: '{query}'")
                return {"error": "No business found matching that name and location."}
            
            # The new API returns a list, we'll take the first, most relevant result
            # We rename the keys to match what the AI was expecting from the old library, for consistency.
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