import sys
import json
from pathlib import Path
import httpx # Use httpx for async HTTP requests
import urllib.parse # For URL encoding parameters

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from llm_integrations.google_gemini_client import GoogleGeminiClient # Still needed for search_internet
from config.app_config import app_config # For GOOGLE_API_KEY
from common.logger_setup import setup_logger

logger = setup_logger(__name__)

async def search_internet(query: str) -> str:
    """
    Performs a general internet search using Google Gemini with search grounding.
    """
    logger.info(f"Performing internet search for query: '{query}'")
    gemini_client = GoogleGeminiClient()
    try:
        results = await gemini_client.generate_grounded_response(prompt=query)
        logger.info(f"Search results for '{query}': {results[:200]}...") 
        return results if results else "No information found."
    except Exception as e:
        logger.error(f"Error during internet search for query '{query}': {e}", exc_info=True)
        return f"Error performing internet search: {str(e)}"

async def get_authoritative_business_info(business_name: str, location_context: str) -> str:
    """
    Gets authoritative information for a specific business using the Google Places API (New)
    via direct HTTP requests.
    Args:
        business_name: The name of the business.
        location_context: The city, state, or general area (e.g., 'Kigali, Rwanda').
    Returns:
        A JSON string containing business details, or an error message.
    """
    logger.info(f"Getting authoritative business info for: '{business_name}' in '{location_context}' via direct Places API call.")
    
    if not app_config.GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY is not configured. Cannot call Places API.")
        return json.dumps({"error": "GOOGLE_API_KEY is not configured."})

    # Using the Text Search (New) endpoint
    search_url = "https://places.googleapis.com/v1/places:searchText"
    
    # Construct the request body
    # For textSearch, the query should include both business name and location context
    text_query = f"{business_name}, {location_context}"
    
    request_body = {
        "textQuery": text_query,
        "languageCode": "en", # Optional: specify language
        # "regionCode": "RW", # Optional: specify region for biasing, e.g., Rwanda
        # "maxResultCount": 1 # We typically want the top match
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": app_config.GOOGLE_API_KEY,
        # FieldMask specifies which fields to return. This is crucial for the new API.
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.regularOpeningHours,places.websiteUri,places.rating,places.id"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(search_url, json=request_body, headers=headers)
            response.raise_for_status() # Will raise an exception for 4XX/5XX errors
            
            data = response.json()
            logger.debug(f"Raw Places API response for '{text_query}': {data}")

            if data.get("places") and len(data["places"]) > 0:
                # Return the first place found, as it's usually the most relevant
                # The fields returned are controlled by X-Goog-FieldMask
                place_info = data["places"][0]
                
                # You might want to simplify or re-structure `place_info` before returning
                # For example, regularOpeningHours can be complex.
                # For now, return the direct relevant part of the API response.
                logger.info(f"Successfully retrieved details for '{business_name}': {place_info}")
                return json.dumps(place_info) # Return the first place's details
            else:
                logger.warning(f"No places found for '{text_query}' using Places API.")
                return json.dumps({"error": f"No authoritative information found for '{text_query}'."})

    except httpx.HTTPStatusError as e:
        error_content = e.response.text
        logger.error(f"HTTP error calling Places API for '{text_query}': {e.response.status_code} - {error_content}", exc_info=True)
        return json.dumps({"error": f"Places API HTTP Error {e.response.status_code}: {error_content}"})
    except Exception as e:
        logger.error(f"Error getting authoritative business info for '{text_query}': {e}", exc_info=True)
        return json.dumps({"error": f"Error getting business information: {str(e)}"})

if __name__ == '__main__':
    import asyncio

    async def main_test():
        print("Testing search_internet:")
        results_general = await search_internet(query="What is the weather like in Kigali today?")
        print(f"General Search Results:\n{results_general}\n")

        print("\nTesting get_authoritative_business_info (Direct Places API):")
        # This will make a real API call if your GOOGLE_API_KEY is set up and Places API (New) is enabled.
        business_info = await get_authoritative_business_info(business_name="Green Hills Academy", location_context="Kigali, Rwanda")
        print(f"Business Info for Green Hills Academy, Kigali:\n{business_info}\n")

        business_info_peponi = await get_authoritative_business_info(business_name="Peponi Living Spaces", location_context="Kigali, Rwanda")
        print(f"Business Info for Peponi Living Spaces, Kigali:\n{business_info_peponi}\n")
        
        business_info_nonexistent = await get_authoritative_business_info(business_name="Definitely Not A Real Business XYZ", location_context="Mars")
        print(f"Business Info for Nonexistent Business:\n{business_info_nonexistent}\n")

    asyncio.run(main_test())