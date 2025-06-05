import sqlite3
import json
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
import sys
from pathlib import Path
# Add the project root to the Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Assuming app_config and Pydantic models are in expected locations
from config.app_config import app_config
from database.models import Task, TaskCreate, Call, CallCreate, CallTranscript, CallTranscriptCreate, CallEvent, CallEventCreate, DNDEntry, DNDEntryCreate

DATABASE_FILE = app_config.DATABASE_URL.split("sqlite:///./")[-1] # Gets filename like 'opendeep_app.db'

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # Access columns by name
    conn.execute("PRAGMA foreign_keys = ON;") # Enforce foreign key constraints
    return conn

def initialize_database():
    """Creates database tables from schema.sql if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # Read schema.sql (assuming it's in the same directory or a known path)
    schema_path = Path(__file__).parent / "schema.sql"
    try:
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        cursor.executescript(schema_sql)
        conn.commit()
        print("Database initialized/verified successfully.")
    except FileNotFoundError:
        print(f"Error: schema.sql not found at {schema_path}. Database not initialized.")
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()

# --- Task Operations ---
def create_task(task_data: TaskCreate) -> Optional[int]:
    """Creates a new task and returns its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Ensure next_action_time is set if not provided, defaulting to initial_schedule_time
        next_action_time = task_data.next_action_time or task_data.initial_schedule_time

        cursor.execute("""
            INSERT INTO tasks (user_task_description, generated_agent_prompt, phone_number,
                               initial_schedule_time, business_name, person_name, status,
                               next_action_time, max_attempts, current_attempt_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task_data.user_task_description, task_data.generated_agent_prompt,
              task_data.phone_number, task_data.initial_schedule_time,
              task_data.business_name, task_data.person_name, task_data.status,
              next_action_time, task_data.max_attempts, task_data.current_attempt_count))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error in create_task: {e}")
        return None
    finally:
        conn.close()

def get_task_by_id(task_id: int) -> Optional[Task]:
    """Retrieves a task by its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return Task(**row) if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_task_by_id: {e}")
        return None
    finally:
        conn.close()

def update_task_status(task_id: int, status: str,
                       next_action_time: Optional[datetime] = None,
                       overall_conclusion: Optional[str] = None,
                       increment_attempt_count: bool = False) -> bool:
    """Updates a task's status, optionally next_action_time, conclusion, and attempt count."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        fields_to_update = ["status = ?"]
        params: List[Any] = [status]

        if next_action_time is not None:
            fields_to_update.append("next_action_time = ?")
            params.append(next_action_time)
        if overall_conclusion is not None:
            fields_to_update.append("overall_conclusion = ?")
            params.append(overall_conclusion)
        if increment_attempt_count:
            fields_to_update.append("current_attempt_count = current_attempt_count + 1")
            # No param needed for this part

        params.append(task_id)
        query = f"UPDATE tasks SET {', '.join(fields_to_update)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"

        cursor.execute(query, tuple(params))
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Database error in update_task_status: {e}")
        return False
    finally:
        conn.close()

def get_due_tasks(max_tasks: int = 10) -> List[Task]:
    """Retrieves tasks that are due for action."""
    conn = get_db_connection()
    tasks = []
    try:
        cursor = conn.cursor()
        # Tasks that are 'pending' or 'on_hold', whose next_action_time is past or null (for immediate pending),
        # and haven't exceeded max attempts.
        cursor.execute("""
            SELECT * FROM tasks
            WHERE (status = 'pending' OR status = 'on_hold')
              AND (next_action_time IS NULL OR next_action_time <= CURRENT_TIMESTAMP)
              AND current_attempt_count < max_attempts
            ORDER BY next_action_time ASC, created_at ASC
            LIMIT ?
        """, (max_tasks,))
        for row in cursor.fetchall():
            tasks.append(Task(**row))
    except sqlite3.Error as e:
        print(f"Database error in get_due_tasks: {e}")
    finally:
        conn.close()
    return tasks

# --- Call Attempt Operations ---
def create_call_attempt(call_data: CallCreate) -> Optional[int]:
    """Creates a new call attempt record and returns its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO calls (task_id, attempt_number, scheduled_time, status, prompt_used)
            VALUES (?, ?, ?, ?, ?)
        """, (call_data.task_id, call_data.attempt_number, call_data.scheduled_time,
              call_data.status, call_data.prompt_used))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error in create_call_attempt: {e}")
        return None
    finally:
        conn.close()

def get_call_attempt_by_id(call_id: int) -> Optional[Call]:
    """Retrieves a call attempt by its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
        row = cursor.fetchone()
        return Call(**row) if row else None
    except sqlite3.Error as e:
        print(f"Database error in get_call_attempt_by_id: {e}")
        return None
    finally:
        conn.close()

def update_call_attempt_status(call_id: int, status: str,
                               call_conclusion: Optional[str] = None,
                               duration_seconds: Optional[int] = None,
                               hangup_cause: Optional[str] = None,
                               asterisk_channel: Optional[str] = None,
                               call_uuid: Optional[str] = None
                               ) -> bool:
    """Updates a call attempt's status and other details."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        fields = {"status": status, "updated_at": "CURRENT_TIMESTAMP"}
        if call_conclusion is not None: fields["call_conclusion"] = call_conclusion
        if duration_seconds is not None: fields["duration_seconds"] = duration_seconds
        if hangup_cause is not None: fields["hangup_cause"] = hangup_cause
        if asterisk_channel is not None: fields["asterisk_channel"] = asterisk_channel
        if call_uuid is not None: fields["call_uuid"] = call_uuid

        set_clauses = [f"{key} = ?" for key in fields]
        params = list(fields.values())
        params.append(call_id)

        query = f"UPDATE calls SET {', '.join(set_clauses)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Database error in update_call_attempt_status: {e}")
        return False
    finally:
        conn.close()

# --- Transcript Operations ---
def log_transcript_entry(transcript_data: CallTranscriptCreate) -> Optional[int]:
    """Logs a transcript entry and returns its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO call_transcripts (call_id, speaker, message)
            VALUES (?, ?, ?)
        """, (transcript_data.call_id, transcript_data.speaker, transcript_data.message))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error in log_transcript_entry: {e}")
        return None
    finally:
        conn.close()

def get_transcripts_for_call(call_id: int) -> List[CallTranscript]:
    """Retrieves all transcript entries for a given call_id."""
    conn = get_db_connection()
    transcripts = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM call_transcripts WHERE call_id = ? ORDER BY timestamp ASC", (call_id,))
        for row in cursor.fetchall():
            transcripts.append(CallTranscript(**row))
    except sqlite3.Error as e:
        print(f"Database error in get_transcripts_for_call: {e}")
    finally:
        conn.close()
    return transcripts


# --- Call Event Operations ---
def log_call_event(event_data: CallEventCreate) -> Optional[int]:
    """Logs a call event and returns its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        details_json = json.dumps(event_data.details) if event_data.details is not None else None
        cursor.execute("""
            INSERT INTO call_events (call_id, event_type, details)
            VALUES (?, ?, ?)
        """, (event_data.call_id, event_data.event_type, details_json))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error in log_call_event: {e}")
        return None
    finally:
        conn.close()

# --- DND Operations ---
def add_to_dnd(dnd_data: DNDEntryCreate) -> Optional[int]:
    """Adds a phone number to the DND list."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO dnd_list (phone_number, reason, task_id)
            VALUES (?, ?, ?)
        """, (dnd_data.phone_number, dnd_data.reason, dnd_data.task_id))
        conn.commit()
        # If INSERT OR IGNORE didn't insert (already exists), lastrowid might be 0 or None.
        # We might want to fetch the ID if it already exists or just confirm operation.
        # For now, returning lastrowid is fine for new entries.
        return cursor.lastrowid if cursor.rowcount > 0 else True # True if exists or inserted
    except sqlite3.Error as e:
        print(f"Database error in add_to_dnd: {e}")
        return None
    finally:
        conn.close()

def is_on_dnd(phone_number: str) -> bool:
    """Checks if a phone number is on the DND list."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM dnd_list WHERE phone_number = ?", (phone_number,))
        return cursor.fetchone() is not None
    except sqlite3.Error as e:
        print(f"Database error in is_on_dnd: {e}")
        return False # Fail safe: assume not on DND if error
    finally:
        conn.close()


# --- Helper for path resolution relative to this file ---
from pathlib import Path

if __name__ == "__main__":
    print(f"Using database file: {DATABASE_FILE}")
    initialize_database() # Ensure tables are created

    # Basic Test
    print("\n--- Testing db_manager ---")
    # Task Creation
    test_task_data = TaskCreate(
        user_task_description="Test task: call user about product",
        generated_agent_prompt="Hello, this is a test call.",
        phone_number="555000111",
        initial_schedule_time=datetime.now(),
        business_name="TestCorp",
        next_action_time=datetime.now() # Explicitly set for testing get_due_tasks
    )
    task_id = create_task(test_task_data)
    if task_id:
        print(f"Created task with ID: {task_id}")
        retrieved_task = get_task_by_id(task_id)
        if retrieved_task:
            print(f"Retrieved task: {retrieved_task.user_task_description}")

            # Update Task Status
            update_success = update_task_status(task_id, "in-progress", increment_attempt_count=True)
            print(f"Task status update successful: {update_success}")
            updated_task = get_task_by_id(task_id)
            if updated_task:
                print(f"Updated task status: {updated_task.status}, attempts: {updated_task.current_attempt_count}")

            # Call Attempt Creation
            test_call_data = CallCreate(
                task_id=task_id,
                attempt_number=1,
                scheduled_time=datetime.now(),
                status="pending_initiation",
                prompt_used=updated_task.generated_agent_prompt
            )
            call_id = create_call_attempt(test_call_data)
            if call_id:
                print(f"Created call attempt with ID: {call_id}")
                # Log Transcript
                transcript_id = log_transcript_entry(CallTranscriptCreate(call_id=call_id, speaker="agent", message="Hello from agent!"))
                print(f"Logged transcript entry ID: {transcript_id}")
                transcript_id_user = log_transcript_entry(CallTranscriptCreate(call_id=call_id, speaker="user", message="Hello agent!"))
                print(f"Logged transcript entry ID: {transcript_id_user}")

                transcripts = get_transcripts_for_call(call_id)
                print(f"Retrieved {len(transcripts)} transcripts for call {call_id}:")
                for t in transcripts:
                    print(f"  {t.speaker}: {t.message}")

                # Log Event
                event_id = log_call_event(CallEventCreate(call_id=call_id, event_type="dtmf_sent", details={"digit": "1"}))
                print(f"Logged call event ID: {event_id}")

                # Update Call Attempt Status
                update_call_success = update_call_attempt_status(call_id, "completed_attempt", call_conclusion="User answered.", duration_seconds=60, call_uuid="test-uuid-123")
                print(f"Call attempt status update successful: {update_call_success}")
                updated_call_attempt = get_call_attempt_by_id(call_id)
                if updated_call_attempt:
                     print(f"Updated call attempt status: {updated_call_attempt.status}, UUID: {updated_call_attempt.call_uuid}")

            # Test DND
            dnd_added = add_to_dnd(DNDEntryCreate(phone_number="555000111", reason="Test DND", task_id=task_id))
            print(f"Added to DND: {dnd_added}")
            is_dnd = is_on_dnd("555000111")
            print(f"Is 555000111 on DND: {is_dnd}")
            is_not_dnd = is_on_dnd("555222333")
            print(f"Is 555222333 on DND: {is_not_dnd}")

        # Test get_due_tasks
        # To make the above task "due" for this test, ensure its next_action_time is in the past.
        # Or create a new one specifically for this.
        print("\nFetching due tasks...")
        due_tasks = get_due_tasks()
        print(f"Found {len(due_tasks)} due tasks.")
        for dt in due_tasks:
            print(f"  Due Task ID: {dt.id}, Phone: {dt.phone_number}, Next Action: {dt.next_action_time}")

    else:
        print("Failed to create task.")