from pydantic import BaseModel, Field, constr
from typing import Optional, List, Dict, Any
from datetime import datetime

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
    status: str = Field("pending", examples=["pending", "in-progress", "completed", "failed"])
    final_summary_report: Optional[str] = None

class CampaignCreate(BaseModel):
    user_id: int
    batch_id: str
    user_goal_description: str

class Campaign(CampaignBase):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True


# --- Task Models (now with campaign_id) ---
class TaskBase(BaseModel):
    campaign_id: int
    user_task_description: str # This will be the original campaign goal
    generated_agent_prompt: str # The specific prompt for this single call
    phone_number: constr(strip_whitespace=True, min_length=7, max_length=20)
    initial_schedule_time: datetime
    business_name: Optional[str] = None
    person_name: Optional[str] = None
    status: str = Field("pending", examples=["pending", "in-progress", "completed", "failed_conclusive", "on_hold", "pending_analysis"])
    overall_conclusion: Optional[str] = None
    next_action_time: Optional[datetime] = None
    max_attempts: int = Field(3, gt=0)
    current_attempt_count: int = Field(0, ge=0)

class TaskCreate(TaskBase):
    pass

class Task(TaskBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True


# --- Call, Transcript, Event, DND Models (No changes needed from your version) ---
class CallBase(BaseModel):
    task_id: int
    attempt_number: int
    scheduled_time: datetime
    status: str = Field("pending_initiation", examples=["pending_initiation", "dialing", "in-progress", "completed_attempt", "failed_attempt", "rescheduled_by_agent"])
    asterisk_channel: Optional[str] = None
    call_uuid: Optional[str] = None
    prompt_used: Optional[str] = None
    call_conclusion: Optional[str] = None
    hangup_cause: Optional[str] = None
    duration_seconds: Optional[int] = None

class CallCreate(CallBase):
    pass

class Call(CallBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class CallTranscriptBase(BaseModel):
    call_id: int
    speaker: constr(strip_whitespace=True, pattern=r"^(user|agent|system)$")
    message: str

class CallTranscriptCreate(CallTranscriptBase):
    pass

class CallTranscript(CallTranscriptBase):
    id: int
    timestamp: datetime
    class Config:
        from_attributes = True

class CallEventBase(BaseModel):
    call_id: int
    event_type: str
    details: Optional[Dict[str, Any]] = None

class CallEventCreate(CallEventBase):
    pass

class CallEvent(CallEventBase):
    id: int
    timestamp: datetime
    class Config:
        from_attributes = True

class DNDEntryBase(BaseModel):
    phone_number: constr(strip_whitespace=True, min_length=7, max_length=20)
    reason: Optional[str] = None
    task_id: Optional[int] = None

class DNDEntryCreate(DNDEntryBase):
    pass

class DNDEntry(DNDEntryBase):
    id: int
    added_at: datetime
    class Config:
        from_attributes = True