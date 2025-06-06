import sys
from pathlib import Path
from typing import List, Dict, Any

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel

from task_manager.ui_assistant_svc import UIAssistantService
from common.logger_setup import setup_logger
from config.app_config import app_config

# --- Setup ---
logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)
router = APIRouter(
    prefix="/api",
    tags=["API"]
)

# --- Pydantic Models for this endpoint ---
class ChatRequest(BaseModel):
    username: str
    chat_history: List[Dict[str, Any]]

# --- API Endpoint ---

@router.post("/chat_interaction")
async def handle_chat_interaction(chat_request: ChatRequest, fastapi_request: Request):
    """
    Handles a turn in the chat conversation with the UI Assistant.
    Receives the entire chat history and returns the assistant's next structured response.
    """
    try:
        # Get the service instance from the app state
        ui_assistant: UIAssistantService = fastapi_request.app.state.ui_assistant_service
        
        # The username isn't used by the service yet, but is here for future multi-tenancy logic
        logger.info(f"Received chat interaction for user '{chat_request.username}'")

        if not chat_request.chat_history:
            raise HTTPException(status_code=400, detail="Chat history cannot be empty.")

        # Get the next structured response from the service
        assistant_response = await ui_assistant.get_next_chat_response(
            chat_history=chat_request.chat_history
        )

        if assistant_response.get("status") == "error":
             raise HTTPException(status_code=500, detail=assistant_response.get("message", "Unknown error in UI Assistant Service."))

        return assistant_response

    except HTTPException:
        raise # Re-raise FastAPI's own exceptions
    except Exception as e:
        logger.error(f"Error in /chat_interaction endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred."
        )

# NOTE: The endpoint for actually *scheduling* the campaign will be created in Phase 2,
# once the Orchestrator Service is built. The UI will trigger it then.