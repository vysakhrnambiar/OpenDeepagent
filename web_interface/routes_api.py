import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException 
# Removed 'Request' as it's not strictly needed for the Pydantic flow if not doing raw body access

# --- Path Hack (Ensure this is present if running scripts directly within web_interface sometimes,
# but main.py at the root should handle global path setup) ---
# For robustness, we can keep it, but it shouldn't be strictly necessary when running via `python main.py`
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

from task_manager.ui_assistant_svc import UIAssistantService
from task_manager.orchestrator_svc import OrchestratorService
from database.db_manager import get_or_create_user
from common.data_models import ChatInteractionRequest, CampaignExecutionRequest # These are the key Pydantic models
from common.logger_setup import setup_logger

logger = setup_logger(__name__) # Sets up a logger specific to this routes_api module
router = APIRouter()

@router.post("/chat_interaction")
async def chat_interaction(request_data: ChatInteractionRequest): # Corrected: Use the Pydantic model directly
    """
    Handles a user's chat message, interacts with the UIAssistantService,
    and returns the AI's response.
    """
    logger.info(f"Received chat interaction for user: {request_data.username}. Message: '{request_data.message[:50]}...'")
    try:
        assistant = UIAssistantService(username=request_data.username)
        response_data = await assistant.process_user_message(
            message=request_data.message,
            history=request_data.history
        )
        logger.debug(f"Chat interaction response for {request_data.username}: {str(response_data)[:200]}...")
        return response_data
    except Exception as e:
        logger.error(f"Error in chat_interaction endpoint for user {request_data.username}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")

@router.post("/execute_campaign")
async def execute_campaign(request_data: CampaignExecutionRequest):
    """
    Receives the final campaign plan from the UI, validates the user,
    and hands off the plan to the OrchestratorService to create tasks in the DB.
    """
    logger.info(f"Received request to execute campaign for user: {request_data.username}")
    try:
        # 1. Get or create the user record from the database.
        user = get_or_create_user(request_data.username)
        if not user:
            logger.error(f"Could not get or create user '{request_data.username}'. Aborting campaign execution.")
            # Return a more user-friendly error or specific error code if needed
            raise HTTPException(status_code=400, detail="User could not be identified or created.")

        # 2. Instantiate the OrchestratorService with the validated user's ID.
        # Ensure OrchestratorService is correctly imported and initialized
        orchestrator = OrchestratorService(user_id=user.id)

        # 3. Call the orchestrator to process the plan and create DB records.
        result = await orchestrator.execute_plan(request_data.campaign_plan)

        # 4. Check the result and return an appropriate response to the frontend.
        if result.get("status") == "success":
            logger.info(f"Successfully executed campaign plan for user {user.username}. Result: {result.get('message')}")
            return {"status": "success", "message": result.get("message", "Campaign scheduled.")}
        else:
            logger.error(f"Failed to execute campaign plan for user {user.username}. Reason: {result.get('message')}")
            raise HTTPException(status_code=500, detail=result.get("message", "An unknown error occurred during campaign orchestration."))

    except HTTPException:
        raise # Re-raise HTTPException directly if it's already one (like from user creation failure)
    except Exception as e:
        logger.error(f"Critical error in execute_campaign endpoint for user {request_data.username}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"A critical server error occurred: {str(e)}")