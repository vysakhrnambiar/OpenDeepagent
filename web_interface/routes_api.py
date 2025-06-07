# web_interface/routes_api.py

import sys
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any

# --- Path Hack ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from database.db_manager import get_or_create_user
from database.models import User
from task_manager.ui_assistant_svc import UIAssistantService
from common.logger_setup import setup_logger
from config.app_config import app_config

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

router = APIRouter()

class ChatInteractionRequest(BaseModel):
    username: str
    chat_history: List[Dict[str, str]]

# This is a placeholder that will be overridden by the main app.
# It's the key to breaking the circular import.
def get_assistant_dependency() -> UIAssistantService:
    raise NotImplementedError("This dependency must be overridden by the main app.")

@router.post("/chat_interaction")
async def handle_chat_interaction(
    chat_request: ChatInteractionRequest,
    ui_assistant: UIAssistantService = Depends(get_assistant_dependency)
):
    logger.info(f"Received chat interaction for user '{chat_request.username}'")
    try:
        user: User | None = get_or_create_user(username=chat_request.username)
        if not user:
            raise HTTPException(status_code=500, detail="Could not get or create user.")

        assistant_response = await ui_assistant.process_chat_interaction(
            user_id=user.id,
            conversation_history=chat_request.chat_history
        )

        if not assistant_response or "status" not in assistant_response:
            logger.error("UIAssistantService returned an invalid response.")
            raise HTTPException(status_code=500, detail="Received an invalid response from the AI assistant.")

        return assistant_response

    except Exception as e:
        logger.error(f"Error in /chat_interaction endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))