# common/data_models.py

import sys
from pathlib import Path
from typing import Optional, Dict, Any, List, Literal # Added Literal
from datetime import datetime
from pydantic import BaseModel, Field, constr

# --- Path Hack for direct execution (if needed for testing this file) ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

# --- API Request Models ---

class WebTaskRefinementDetails(BaseModel):
    """
    Model for sending task description and current details to the API
    for generating or refining an agent prompt.
    """
    user_task_description: str = Field(..., description="The user's description of the task for the AI agent.")
    current_collected_details: Optional[Dict[str, Any]] = Field(None, description="Details already collected from the user in previous steps, if any.")
    # Example for current_collected_details: {"phone_number": "1234567890", "preferred_date": "tomorrow"}


# --- THIS MODEL IS NOW SIMPLER ---
class WebScheduleTaskRequest(BaseModel):
    """
    Model for scheduling a new task. The backend will parse details from the prompts.
    """
    user_task_description: str = Field(..., description="The original task description provided by the user.")
    generated_agent_prompt: str = Field(..., description="The finalized prompt for the AI agent.")

# --- API Response Models --- (No changes here)
class ApiResponse(BaseModel):
    """
    Generic API response model.
    """
    success: bool = Field(..., description="Indicates if the operation was successful.")
    message: Optional[str] = Field(None, description="A message providing more details, especially on failure.")
    data: Optional[Dict[str, Any]] = Field(None, description="Optional data payload, e.g., created resource ID.")
    # Example for data: {"task_id": 123}

class GeneratedPromptResponse(BaseModel):
    """
    Response from the agent prompt generation endpoint.
    """
    status: str = Field(..., description="Status of the prompt generation (e.g., 'needs_more_info', 'prompt_generated', 'error').")
    questions_for_user: Optional[str] = Field(None, description="Questions for the user if more information is needed.")
    agent_prompt: Optional[str] = Field(None, description="The generated agent prompt if successful.")
    message: Optional[str] = Field(None, description="Error message if the status is 'error'.")
    raw_llm_output: Optional[str] = Field(None, description="Raw output from LLM for debugging if format was unexpected.")

class TaskBasicInfo(BaseModel):
    id: int
    user_task_description: str
    phone_number: str
    status: str
    next_action_time: Optional[datetime] = None
    overall_conclusion: Optional[str] = None
    current_attempt_count: int
    max_attempts: int

class TaskListResponse(ApiResponse):
    """
    API response for listing tasks.
    'data' field will contain a list of tasks.
    Using a more specific data type here.
    """
    success: bool
    data: Optional[List[TaskBasicInfo]] = None
    total_tasks: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None


# --- Redis Command Message Models (basic structure for now) ---

class RedisCommandBase(BaseModel):
    command_type: str

class RedisDTMFCommand(RedisCommandBase):
    command_type: Literal["send_dtmf"] = "send_dtmf"
    call_attempt_id: int # To identify which call this command is for
    digits: str = Field(..., description="DTMF digits to send (0-9, *, #).")

class RedisEndCallCommand(RedisCommandBase):
    command_type: Literal["end_call"] = "end_call"
    call_attempt_id: int
    reason: str = Field("AI decided to end the call.", description="Reason for ending the call.")
    # outcome: str # This might be determined by post-call analysis, or agent signals simple success/failure

class RedisRescheduleCommand(RedisCommandBase):
    command_type: Literal["reschedule_call_trigger_analysis"] = "reschedule_call_trigger_analysis"
    call_attempt_id: int
    reason: str
    time_description: str # e.g., "tomorrow morning", "in 2 hours"
