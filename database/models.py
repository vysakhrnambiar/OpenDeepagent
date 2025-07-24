# database/models.py
from pydantic import BaseModel, Field
from pydantic.types import constr
from typing import Optional, List, Dict, Any, Annotated
from datetime import datetime
from enum import Enum # Import Enum

from config.app_config import app_config

# --- Status Enums ---
class TaskStatus(str, Enum):
    PENDING = "pending"                     # Task created, awaiting scheduling
    QUEUED_FOR_CALL = "queued_for_call"     # Task picked by TaskScheduler, DND checked, ready for CallInitiator
    INITIATING_CALL = "initiating_call"     # CallInitiator is creating Call record, preparing to trigger CallAttemptHandler
    RETRY_SCHEDULED = "retry_scheduled"     # PostCallAnalyzer determined a retry is needed and set next_action_time
    # IN_PROGRESS might be a status for the Task if a call attempt is active, or we might rely on CallStatus for that
    PENDING_ANALYSIS = "pending_analysis"   # Call attempt ended, awaiting PostCallAnalyzer
    PENDING_USER_INFO = "pending_user_info" # AI requested information from task creator, awaiting response
    COMPLETED_SUCCESS = "completed_success" # Task objective fully met
    COMPLETED_FAILURE = "completed_failure" # Task objective could not be met after all attempts or due to conclusive failure
    ON_HOLD = "on_hold"                     # Task manually or system-paused
    CANCELLED_DND = "cancelled_dnd"         # Cancelled due to DND list
    CANCELLED_USER = "cancelled_user"       # Cancelled by user action
    ERROR = "error"                         # Generic error state for the task

class CallStatus(str, Enum):
    PENDING_ORIGINATION = "pending_origination" # Call record created by CallInitiator, CallAttemptHandler will send to Asterisk
    ORIGINATING = "originating"                 # CallAttemptHandler has sent Originate to Asterisk
    DIALING = "dialing"                         # Asterisk AMI reports dialing
    RINGING = "ringing"                         # Asterisk AMI reports remote end ringing
    ANSWERED = "answered"                       # Asterisk AMI reports call answered (AudioSocket connection expected)
    LIVE_AI_HANDLING = "live_ai_handling"       # AudioSocket connected, OpenAIRealtimeClient active
    COMPLETED_AI_OBJECTIVE_MET = "completed_ai_objective_met"     # AI believes it met its specific goal for THIS call attempt
    COMPLETED_AI_HANGUP = "completed_ai_hangup"                   # AI decided to hang up (e.g., user asked to end, goal not met but cannot proceed)
    COMPLETED_USER_HANGUP = "completed_user_hangup"               # Remote user hung up
    COMPLETED_SYSTEM_HANGUP = "completed_system_hangup"           # System (e.g., CallAttemptHandler due to timeout) initiated hangup
    FAILED_NO_ANSWER = "failed_no_answer"
    FAILED_BUSY = "failed_busy"
    FAILED_CONGESTION = "failed_congestion"
    FAILED_INVALID_NUMBER = "failed_invalid_number"
    FAILED_CHANNEL_UNAVAILABLE = "failed_channel_unavailable"
    FAILED_ASTERISK_ERROR = "failed_asterisk_error"               # Error reported by Asterisk during attempt
    FAILED_INTERNAL_ERROR = "failed_internal_error"             # Error within our system during the call attempt

# --- User Models ---
class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    pass

class User(UserBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True


# --- Campaign Models ---
class CampaignBase(BaseModel):
    user_id: int
    batch_id: str
    user_goal_description: str
    status: str = Field("pending", examples=["pending", "in-progress", "completed", "failed"]) # Consider a CampaignStatus enum later if needed
    final_summary_report: Optional[str] = None

class CampaignCreate(BaseModel): # No status on create, defaults in DB or service
    user_id: int
    batch_id: str
    user_goal_description: str


class Campaign(CampaignBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True


# --- Task Models (now with campaign_id and TaskStatus enum) ---
class TaskBase(BaseModel):
    campaign_id: int
    user_id: int # Added for easier querying and ensuring data belongs to the user
    user_task_description: str
    generated_agent_prompt: str
    phone_number: Annotated[str, Field(min_length=7, max_length=20)]
    initial_schedule_time: datetime
    business_name: Optional[str] = None
    person_name: Optional[str] = None
    status: TaskStatus = Field(TaskStatus.PENDING) # Use Enum, default to PENDING
    overall_conclusion: Optional[str] = None
    next_action_time: Optional[datetime] = None
    max_attempts: int = Field(3, gt=0)
    current_attempt_count: int = Field(0, ge=0)
    # HITL (Human-in-the-Loop) support fields
    user_info_request: Optional[str] = None          # The question asked to the task creator
    user_info_response: Optional[str] = None         # The response from the task creator
    user_info_timeout: int = Field(10, gt=0)         # Timeout in seconds for user response
    user_info_requested_at: Optional[datetime] = None # When the request was made
    # inter_call_context: Optional[str] = None # For TaskLifecycleManager later

class TaskCreate(BaseModel): # Separate create model if defaults differ or some fields aren't set on creation
    campaign_id: int
    user_id: int
    user_task_description: str
    generated_agent_prompt: str
    phone_number: Annotated[str, Field(min_length=7, max_length=20)]
    initial_schedule_time: datetime
    business_name: Optional[str] = None
    person_name: Optional[str] = None
    # status: TaskStatus = TaskStatus.PENDING # Default set in TaskBase or DB
    next_action_time: Optional[datetime] = None # Often same as initial_schedule_time at creation
    max_attempts: int = Field(app_config.DEFAULT_MAX_TASK_ATTEMPTS, gt=0) # Use app_config default
    # current_attempt_count is 0 by default in DB
    # HITL fields are optional for task creation (will be set later during calls)
    user_info_timeout: int = Field(10, gt=0)         # Timeout in seconds for user response

class Task(TaskBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True


# --- Task Event Models ---
class TaskEventBase(BaseModel):
    task_id: int
    event_type: str = Field(..., description="Type of event (e.g., 'status_changed', 'retry_scheduled', 'user_info_requested')")
    event_details: Optional[str] = Field(None, description="JSON string with event-specific data")
    created_by: str = Field("system", description="Who/what created this event")

class TaskEventCreate(TaskEventBase):
    pass

class TaskEvent(TaskEventBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True


# --- Call, Transcript, Event, DND Models (now with CallStatus enum) ---
class CallBase(BaseModel):
    task_id: int
    attempt_number: int
    # scheduled_time: datetime # This is effectively created_at for the call record
    status: CallStatus = Field(CallStatus.PENDING_ORIGINATION) # Use Enum
    asterisk_channel: Optional[str] = None
    call_uuid: Optional[str] = None
    prompt_used: Optional[str] = None # The specific AI prompt used for this attempt
    call_conclusion: Optional[str] = None # Summary/result specific to this call attempt
    hangup_cause: Optional[str] = None # From Asterisk if available
    duration_seconds: Optional[int] = None

class CallCreate(BaseModel): # Specific model for creating a call attempt
    task_id: int
    attempt_number: int
    status: CallStatus = CallStatus.PENDING_ORIGINATION
    prompt_used: str # Make prompt_used mandatory for creation

class Call(CallBase):
    id: int
    created_at: datetime # Will be set by DB default
    updated_at: datetime # Will be set by DB default/trigger
    class Config:
        from_attributes = True

class CallTranscriptBase(BaseModel):
    call_id: int
    speaker: Annotated[str, Field(pattern=r"^(user|agent|system)$")] # Keep as string for simplicity
    message: str

class CallTranscriptCreate(CallTranscriptBase):
    pass

class CallTranscript(CallTranscriptBase):
    id: int
    timestamp: datetime # Or default in DB
    class Config:
        from_attributes = True

class CallEventBase(BaseModel):
    call_id: int
    event_type: str # Could be an Enum too if event types become well-defined
    details: Optional[Dict[str, Any]] = None # JSON string or Dict

class CallEventCreate(CallEventBase):
    pass

class CallEvent(CallEventBase):
    id: int
    timestamp: datetime # Or default in DB
    class Config:
        from_attributes = True

class DNDEntryBase(BaseModel):
    user_id: int # DND lists are per-user
    phone_number: Annotated[str, Field(min_length=7, max_length=20)]
    reason: Optional[str] = None
    # task_id: Optional[int] = None # Link to task that triggered DND if applicable

class DNDEntryCreate(DNDEntryBase):
    pass

class DNDEntry(DNDEntryBase):
    id: int
    added_at: datetime # Or default in DB
    class Config:
        from_attributes = True