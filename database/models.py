from pydantic import BaseModel, Field, constr, validator
from typing import Optional, List, Dict, Any
from datetime import datetime


# Pydantic models can be used for data validation when interacting
# with the database, especially if not using a full ORM like SQLAlchemy initially.
# They also serve as clear data structure definitions.

class TaskBase(BaseModel):
    user_task_description: str
    generated_agent_prompt: str
    phone_number: constr(strip_whitespace=True, min_length=7, max_length=20) # Basic validation
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
        from_attributes = True # or from_attributes = True for Pydantic v2

class CallBase(BaseModel):
    task_id: int
    attempt_number: int
    scheduled_time: datetime # Actual time this attempt was scheduled/initiated
    status: str = Field("pending_initiation", examples=["pending_initiation", "dialing", "in-progress", "completed_attempt", "failed_attempt", "rescheduled_by_agent"])
    asterisk_channel: Optional[str] = None
    call_uuid: Optional[str] = None # Should be unique if present
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
        from_attributes = True# or from_attributes = True for Pydantic v2


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
        from_attributes = True # or from_attributes = True for Pydantic v2


class CallEventBase(BaseModel):
    call_id: int
    event_type: str # e.g., 'dtmf_sent', 'call_started', 'function_called_by_agent'
    details: Optional[Dict[str, Any]] = None # Store as JSON string in DB, parse to dict here

    @validator('details', pre=True)
    def parse_details_json(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # Or raise ValueError, or return the string if that's acceptable
                return {"raw_details": v, "error": "Invalid JSON string"}
        return v


class CallEventCreate(CallEventBase):
    pass

class CallEvent(CallEventBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True # or from_attributes = True for Pydantic v2


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
        from_attributes = True # or from_attributes = True for Pydantic v2

if __name__ == "__main__":
    # Example usage/test
    try:
        task_data = {
            "user_task_description": "Book appointment",
            "generated_agent_prompt": "Hello, I am calling to book...",
            "phone_number": "1234567890",
            "initial_schedule_time": datetime.now(),
            "business_name": "Dental Clinic"
        }
        new_task = TaskCreate(**task_data)
        print("TaskCreate model valid:", new_task.model_dump_json(indent=2))

        call_data = {
            "task_id": 1,
            "attempt_number": 1,
            "scheduled_time": datetime.now(),
            "prompt_used": "Hello, I am calling to book..."
        }
        new_call = CallCreate(**call_data)
        print("\nCallCreate model valid:", new_call.model_dump_json(indent=2))

        transcript_data = {
            "call_id": 1,
            "speaker": "agent",
            "message": "How can I help you?"
        }
        new_transcript = CallTranscriptCreate(**transcript_data)
        print("\nCallTranscriptCreate model valid:", new_transcript.model_dump_json(indent=2))

        # Test speaker validation
        invalid_transcript_data = {**transcript_data, "speaker": "unknown"}
        try:
            CallTranscriptCreate(**invalid_transcript_data)
        except Exception as e:
            print(f"\nCaught expected validation error for speaker: {e}")

        event_data_json_details = {
            "call_id":1,
            "event_type": "dtmf_sent",
            "details": '{"digits": "123", "channel": "SIP/100-123"}' # Details as JSON string
        }
        event1 = CallEventCreate(**event_data_json_details)
        print("\nCallEvent with JSON string details:", event1.model_dump_json(indent=2))
        if isinstance(event1.details, dict):
            print("Details parsed to dict successfully.")

    except Exception as e:
        print(f"Error during Pydantic model testing: {e}")