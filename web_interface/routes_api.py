import sys
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, UploadFile
from pydantic import BaseModel
from typing import Optional, List
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
from database.db_manager import get_or_create_user, get_task_by_id
from database.models import TaskStatus
from common.data_models import ChatInteractionRequest, CampaignExecutionRequest # These are the key Pydantic models
from common.logger_setup import setup_logger
from llm_integrations.openai_audio_client import OpenAIAudioClient

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


@router.post("/transcribe_audio")
async def transcribe_audio(file: UploadFile):
    """
    Receives an audio file, transcribes it, and returns the text.
    """
    logger.info(f"Received audio file for transcription: {file.filename}")
    try:
        audio_client = OpenAIAudioClient()
        transcribed_text = await audio_client.transcribe_audio(file)
        if transcribed_text:
            return {"success": True, "text": transcribed_text}
        else:
            raise HTTPException(status_code=500, detail="Failed to transcribe audio.")
    except Exception as e:
        logger.error(f"Error in transcribe_audio endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")


# HITL API Models
class HITLResponseRequest(BaseModel):
    task_id: int
    response: str
    username: str

class HITLRequest(BaseModel):
    task_id: int
    question: str
    timeout_seconds: int
    call_info: Optional[dict] = None

class PendingHITLResponse(BaseModel):
    success: bool
    requests: List[HITLRequest] = []
    message: Optional[str] = None

# HITL API Endpoints
@router.get("/pending_hitl_requests")
async def get_pending_hitl_requests(username: str = Query(..., description="Username to check for pending requests")):
    """
    Get pending HITL requests for a specific user.
    """
    try:
        # Get user from database
        user = get_or_create_user(username)
        if not user:
            raise HTTPException(status_code=400, detail="User could not be identified or created.")
        
        # Find tasks with pending user info status
        from database.db_manager import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Get tasks that are pending user info for this user
            cursor.execute("""
                SELECT t.id, t.user_info_request, t.user_info_timeout, t.user_info_requested_at,
                       t.phone_number, t.person_name, t.business_name
                FROM tasks t
                WHERE t.user_id = ? AND t.status = ? AND t.user_info_request IS NOT NULL
                ORDER BY t.user_info_requested_at ASC
            """, (user.id, TaskStatus.PENDING_USER_INFO.value))
            
            rows = cursor.fetchall()
            requests = []
            
            for row in rows:
                request = HITLRequest(
                    task_id=row[0],
                    question=row[1] or "",
                    timeout_seconds=row[2] or 10,
                    call_info={
                        "phone_number": row[4],
                        "person_name": row[5],
                        "business_name": row[6]
                    }
                )
                requests.append(request)
            
            return PendingHITLResponse(
                success=True,
                requests=requests,
                message=f"Found {len(requests)} pending requests"
            )
            
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error getting pending HITL requests for user {username}: {e}", exc_info=True)
        return PendingHITLResponse(
            success=False,
            requests=[],
            message=f"Error retrieving pending requests: {str(e)}"
        )

@router.post("/hitl_response")
async def submit_hitl_response(request_data: HITLResponseRequest):
    """
    Submit a response to a HITL request.
    """
    try:
        # Get user from database
        user = get_or_create_user(request_data.username)
        if not user:
            raise HTTPException(status_code=400, detail="User could not be identified or created.")
        
        # Get the task to verify it belongs to this user
        task = get_task_by_id(request_data.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found.")
        
        if task.user_id != user.id:
            raise HTTPException(status_code=403, detail="Task does not belong to this user.")
        
        if task.status != TaskStatus.PENDING_USER_INFO:
            raise HTTPException(status_code=400, detail="Task is not waiting for user information.")
        
        # Import and use the global orchestrator service instance from main
        from main import orchestrator_svc
        if not orchestrator_svc:
            logger.error("Global orchestrator service not available")
            raise HTTPException(status_code=500, detail="HITL service not available")
        
        # Submit the response using the global orchestrator
        success = await orchestrator_svc.handle_task_creator_response(
            task_id=request_data.task_id,
            response=request_data.response
        )
        
        if success:
            logger.info(f"Successfully processed HITL response for task {request_data.task_id} from user {request_data.username}")
            return {"success": True, "message": "Response submitted successfully"}
        else:
            logger.error(f"Failed to process HITL response for task {request_data.task_id} from user {request_data.username}")
            return {"success": False, "message": "Failed to process response"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing HITL response for task {request_data.task_id} from user {request_data.username}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Dashboard API Endpoints

@router.get("/users")
async def get_all_users():
    """Get all users for the dashboard user selector."""
    try:
        from database.db_manager import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT id, username, created_at FROM users ORDER BY username")
            users = []
            for row in cursor.fetchall():
                users.append({
                    "id": row[0],
                    "username": row[1],
                    "created_at": row[2]
                })
            
            return {"success": True, "users": users}
            
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error getting users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/tasks")
async def get_tasks(
    user_id: Optional[int] = Query(None, description="User ID to filter tasks"),
    status: Optional[str] = Query(None, description="Status to filter tasks"),
    phone: Optional[str] = Query(None, description="Phone number to filter tasks"),
    name: Optional[str] = Query(None, description="Contact name to filter tasks"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size")
):
    """Get tasks with optional filters and pagination."""
    try:
        from database.db_manager import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Build query with filters
            where_conditions = []
            params = []
            
            if user_id:
                where_conditions.append("user_id = ?")
                params.append(user_id)
            
            if status:
                where_conditions.append("status = ?")
                params.append(status)
            
            if phone:
                where_conditions.append("phone_number LIKE ?")
                params.append(f"%{phone}%")
            
            if name:
                where_conditions.append("person_name LIKE ?")
                params.append(f"%{name}%")
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Get total count
            count_query = f"SELECT COUNT(*) FROM tasks WHERE {where_clause}"
            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]
            
            # Get tasks with pagination
            offset = (page - 1) * page_size
            tasks_query = f"""
                SELECT id, user_id, user_task_description, phone_number, person_name,
                       status, current_attempt_count, max_attempts, next_action_time,
                       created_at, updated_at, user_info_request, user_info_response
                FROM tasks
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            
            cursor.execute(tasks_query, params + [page_size, offset])
            tasks = []
            
            for row in cursor.fetchall():
                tasks.append({
                    "id": row[0],
                    "user_id": row[1],
                    "user_task_description": row[2],
                    "phone_number": row[3],
                    "person_name": row[4] or "Unknown",
                    "status": row[5],
                    "current_attempt_count": row[6],
                    "max_attempts": row[7],
                    "next_action_time": row[8],
                    "created_at": row[9],
                    "updated_at": row[10],
                    "user_info_request": row[11],
                    "user_info_response": row[12]
                })
            
            total_pages = (total_count + page_size - 1) // page_size
            
            return {
                "success": True,
                "tasks": tasks,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": total_pages
                }
            }
            
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error getting tasks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/tasks/{task_id}/calls")
async def get_task_calls(task_id: int):
    """Get all calls for a specific task."""
    try:
        from database.db_manager import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT id, attempt_number, status, created_at, updated_at,
                       duration_seconds, hangup_cause, call_conclusion
                FROM calls
                WHERE task_id = ?
                ORDER BY attempt_number ASC
            """, (task_id,))
            
            calls = []
            for row in cursor.fetchall():
                calls.append({
                    "id": row[0],
                    "attempt_number": row[1],
                    "status": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                    "duration_seconds": row[5],
                    "hangup_cause": row[6],
                    "call_conclusion": row[7]
                })
            
            return {"success": True, "calls": calls}
            
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error getting calls for task {task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int):
    """Permanently delete a task and all associated data."""
    try:
        from database.db_manager import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # First check if task exists
            cursor.execute("SELECT id, person_name, phone_number, status FROM tasks WHERE id = ?", (task_id,))
            task = cursor.fetchone()
            
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            
            # Begin transaction
            cursor.execute("BEGIN TRANSACTION")
            
            # Delete in proper order due to foreign key constraints
            # 1. Delete call transcripts first
            cursor.execute("""
                DELETE FROM call_transcripts
                WHERE call_id IN (SELECT id FROM calls WHERE task_id = ?)
            """, (task_id,))
            
            # 2. Delete call events
            cursor.execute("""
                DELETE FROM call_events
                WHERE call_id IN (SELECT id FROM calls WHERE task_id = ?)
            """, (task_id,))
            
            # 3. Delete calls
            cursor.execute("DELETE FROM calls WHERE task_id = ?", (task_id,))
            
            # 4. Delete task events
            cursor.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
            
            # 5. Finally delete the task
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            
            conn.commit()
            
            logger.info(f"Successfully deleted task {task_id} ({task[1]} - {task[2]})")
            
            return {
                "success": True,
                "message": f"Task {task_id} has been permanently deleted"
            }
            
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.delete("/clear-database")
async def clear_database(confirm: str = Query(..., description="Must be 'CONFIRM' to proceed")):
    """Clear all database tables with confirmation - DANGER ZONE"""
    if confirm != "CONFIRM":
        raise HTTPException(status_code=400, detail="Confirmation required. Must pass 'CONFIRM' as query parameter.")
    
    try:
        from database.db_manager import clear_all_database_tables, create_database_backup
        
        logger.warning("DATABASE CLEAR OPERATION INITIATED - Creating backup first")
        
        # Create backup before clearing
        backup_file = create_database_backup()
        
        # Clear all tables
        cleared_tables = clear_all_database_tables()
        
        logger.warning(f"DATABASE CLEARED: {cleared_tables} tables emptied. Backup: {backup_file}")
        
        return {
            "success": True,
            "message": f"Database cleared successfully. {cleared_tables} tables emptied.",
            "backup_created": backup_file,
            "tables_cleared": cleared_tables,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error clearing database: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to clear database: {str(e)}")