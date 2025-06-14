import sqlite3
import json
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple # Tuple was missing, added it.
import sys
from pathlib import Path
import uuid # For generating unique batch IDs

# Add the project root to the Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.app_config import app_config
from common.logger_setup import setup_logger # Import logger_setup
from database.models import (
    Task, TaskCreate, Call, CallCreate, CallTranscript, CallTranscriptCreate,
    CallEvent, CallEventCreate, DNDEntry, DNDEntryCreate, User, UserCreate,
    Campaign, CampaignCreate, TaskStatus, CallStatus # Added Enums
)

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL) # Initialize logger for this module

# Use a different DB for testing if needed, but for now, the main one is fine.
DATABASE_FILE = app_config.DATABASE_URL.split("sqlite:///./")[-1]
if "pytest" in sys.modules: # pragma: no cover
     DATABASE_FILE = "test_" + DATABASE_FILE
logger.info(f"Using database file: {DATABASE_FILE}")


def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def initialize_database():
    """Creates database tables from schema.sql if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    schema_path = Path(__file__).parent / "schema.sql"
    try:
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        cursor.executescript(schema_sql)
        conn.commit()
        logger.info("Database initialized/verified successfully.")
    except FileNotFoundError: # pragma: no cover
        logger.error(f"Error: schema.sql not found at {schema_path}. Database not initialized.")
    except Exception as e: # pragma: no cover
        logger.error(f"Error initializing database: {e}", exc_info=True)
    finally:
        conn.close()

# --- User Operations ---


def get_or_create_user(username: str) -> Optional[User]:
    """Retrieves a user by username, creating them if they don't exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return User(**dict(row))

        cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
        conn.commit()
        user_id = cursor.lastrowid
        if user_id is None: # Should not happen with autoincrement but good practice
             logger.error(f"Failed to get lastrowid after inserting user {username}")
             return None


        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        new_row = cursor.fetchone()
        return User(**dict(new_row)) if new_row else None
    except sqlite3.Error as e:
        logger.error(f"Database error in get_or_create_user for {username}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

# --- Campaign Operations ---
def create_campaign(campaign_data: CampaignCreate) -> Optional[Campaign]:
    """Creates a new campaign record and returns the full campaign object."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO campaigns (user_id, batch_id, user_goal_description, status)
            VALUES (?, ?, ?, ?)
        """, (campaign_data.user_id, campaign_data.batch_id, campaign_data.user_goal_description, "pending"))
        conn.commit()
        campaign_id = cursor.lastrowid
        if campaign_id is None:
            logger.error(f"Failed to get lastrowid after inserting campaign for user {campaign_data.user_id}")
            return None

        cursor.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        row = cursor.fetchone()
        return Campaign(**dict(row)) if row else None
    except sqlite3.Error as e:
        logger.error(f"Database error in create_campaign for user {campaign_data.user_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

def create_batch_of_tasks(campaign: Campaign, tasks_data: List[TaskCreate]) -> bool:
    """Creates multiple tasks linked to a single campaign in a transaction."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN TRANSACTION")
        tasks_to_insert = []
        for task_data in tasks_data:
            # When creating tasks from TaskCreate, ensure status is correctly handled.
            # TaskCreate doesn't have a status field by default; it defaults in TaskBase or DB.
            # If TaskCreate needs to override status, it should have a status field.
            # For now, assuming status is defaulted by TaskBase/DB or is appropriately set in task_data.
            status_val = TaskStatus.PENDING.value # Default if not in task_data or handled by model default
            if hasattr(task_data, 'status') and task_data.status:
                 status_val = task_data.status.value if isinstance(task_data.status, Enum) else task_data.status


            tasks_to_insert.append((
                campaign.id,
                task_data.user_id, # Added user_id here
                task_data.user_task_description,
                task_data.generated_agent_prompt,
                task_data.phone_number,
                task_data.initial_schedule_time,
                task_data.business_name,
                task_data.person_name,
                status_val, # Use .value if it's an Enum, else direct string
                task_data.next_action_time,
                task_data.max_attempts,
                0 # current_attempt_count is 0 on creation
            ))

        cursor.executemany("""
            INSERT INTO tasks (campaign_id, user_id, user_task_description, generated_agent_prompt,
                               phone_number, initial_schedule_time, business_name, person_name,
                               status, next_action_time, max_attempts, current_attempt_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, tasks_to_insert)
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Database error in create_batch_of_tasks for campaign {campaign.id}, rolling back: {e}", exc_info=True)
        conn.rollback()
        return False
    finally:
        conn.close()

# --- Task Operations ---
def create_task(task_data: TaskCreate) -> Optional[int]:
    """Creates a single task."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        status_val = TaskStatus.PENDING.value # Default
        if hasattr(task_data, 'status') and task_data.status:
            status_val = task_data.status.value if isinstance(task_data.status, Enum) else task_data.status

        cursor.execute("""
            INSERT INTO tasks (campaign_id, user_id, user_task_description, generated_agent_prompt,
                               phone_number, initial_schedule_time, business_name, person_name,
                               status, next_action_time, max_attempts, current_attempt_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task_data.campaign_id, task_data.user_id, task_data.user_task_description,
              task_data.generated_agent_prompt, task_data.phone_number, task_data.initial_schedule_time,
              task_data.business_name, task_data.person_name, status_val,
              task_data.next_action_time, task_data.max_attempts, 0))
        conn.commit()
        task_id = cursor.lastrowid
        if task_id is None:
            logger.error(f"Failed to get lastrowid after inserting task for campaign {task_data.campaign_id}")
            return None
        return task_id
    except sqlite3.Error as e:
        logger.error(f"Database error in create_task for campaign {task_data.campaign_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

def get_task_by_id(task_id: int) -> Optional[Task]:
    """Retrieves a specific task by its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return Task(**dict(row)) if row else None
    except sqlite3.Error as e:
        logger.error(f"Database error in get_task_by_id for task ID {task_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

# Modified update_task_status to accept TaskStatus Enum
def update_task_status(task_id: int, status: TaskStatus, # Changed to TaskStatus Enum
                       next_action_time: Optional[datetime] = None,
                       overall_conclusion: Optional[str] = None,
                       increment_attempt_count: bool = False) -> bool:
    """Updates the status and other details of a specific task."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        fields_to_update = ["status = ?"]
        params: List[Any] = [status.value] # Use .value for Enum

        if next_action_time is not None:
            fields_to_update.append("next_action_time = ?")
            params.append(next_action_time)
        if overall_conclusion is not None:
            fields_to_update.append("overall_conclusion = ?")
            params.append(overall_conclusion)
        if increment_attempt_count:
            # This should be handled carefully, often PostCallAnalyzer increments this
            # based on whether the attempt was valid.
            fields_to_update.append("current_attempt_count = current_attempt_count + 1")

        params.append(task_id)
        # updated_at is handled by DB trigger
        query = f"UPDATE tasks SET {', '.join(fields_to_update)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        conn.commit()
        updated_rows = cursor.rowcount
        if updated_rows > 0:
            logger.debug(f"Successfully updated task ID {task_id} to status {status.value}")
            return True
        else:
            logger.warning(f"No rows updated for task ID {task_id} when trying to set status {status.value}")
            return False
    except sqlite3.Error as e:
        logger.error(f"Database error in update_task_status for task ID {task_id}: {e}", exc_info=True)
        return False
    finally:
        conn.close()

# Modified get_due_tasks to use TaskStatus Enum for filtering
def get_due_tasks(user_id: Optional[int] = None, max_tasks: int = 10) -> List[Task]:
    """
    Fetches tasks that are due for processing.
    Filters by user_id if provided.
    """
    conn = get_db_connection()
    tasks = []
    try:
        cursor = conn.cursor()
        base_query = """
            SELECT * FROM tasks
            WHERE (status = ? OR status = ? OR status = ?)
              -- AND (next_action_time IS NULL OR next_action_time <= CURRENT_TIMESTAMP) -- Temporarily commented out for testing SQL fetch
              AND current_attempt_count < max_attempts
        """
        params: List[Any] = [
            TaskStatus.PENDING.value,
            TaskStatus.ON_HOLD.value,
            TaskStatus.RETRY_SCHEDULED.value
        ]

        if user_id is not None: # This part is correct and should remain
            base_query += " AND user_id = ?"
            params.append(user_id)
        
        base_query += " ORDER BY next_action_time ASC, created_at ASC LIMIT ?"
        params.append(max_tasks)

        logger.debug(f"get_due_tasks: Executing query: [{base_query.strip()}] with params: {tuple(params)}")
        
        cursor.execute(base_query, tuple(params))
        
        raw_rows = cursor.fetchall()
        logger.debug(f"get_due_tasks: Fetched {len(raw_rows)} raw rows from DB.")

        if not raw_rows:
            logger.debug("get_due_tasks: No raw rows fetched, so loop will not run.")

        for i, row_data in enumerate(raw_rows): # Iterate over fetched rows
            logger.debug(f"get_due_tasks: Processing row {i+1}/{len(raw_rows)}")
            try:
                logger.debug(f"get_due_tasks: Raw row_data type: {type(row_data)}, content (first 100 chars if long): {str(row_data)[:100]}")
                
                row_as_dict = dict(row_data)
                logger.debug(f"get_due_tasks: Row as dict: {row_as_dict}")

                task_obj = Task(**row_as_dict) 
                tasks.append(task_obj)
                logger.debug(f"get_due_tasks: Successfully parsed task ID {task_obj.id} (campaign_id: {task_obj.campaign_id}) into Pydantic model.")
            except Exception as e_parse:
                # Log the dictionary representation of row_data in case of error
                error_row_dict_str = "Could not convert row_data to dict for error logging"
                try:
                    error_row_dict_str = str(dict(row_data))
                except Exception:
                    pass # Keep the default error string
                logger.error(f"get_due_tasks: Error parsing row {i+1}. Data that caused error (potentially as dict): {error_row_dict_str}. Error: {e_parse}", exc_info=True)
                logger.error(f"get_due_tasks: Exception type during parsing: {type(e_parse)}")
            logger.debug(f"get_due_tasks: Finished processing row {i+1}/{len(raw_rows)}.")
                
    except sqlite3.Error as e:
        logger.error(f"Database error in get_due_tasks (user_id: {user_id}): {e}", exc_info=True)
    finally:
        conn.close()
    
    if not tasks:
        logger.debug(f"get_due_tasks: Returning empty list (no tasks met criteria or parsed successfully).")
    else:
        logger.debug(f"get_due_tasks: Returning {len(tasks)} parsed tasks.")
    return tasks

# Add this function to database/db_manager.py (or replace if a similar one exists)

def get_call_by_asterisk_uuid(asterisk_uuid: str) -> Optional[Call]:
    """Retrieves a specific call attempt by its Asterisk call_uuid."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # We query using the 'call_uuid' column which stores the Asterisk UUID
        cursor.execute("SELECT * FROM calls WHERE call_uuid = ?", (asterisk_uuid,))
        row = cursor.fetchone()
        if row:
            return Call(**dict(row)) # Ensure conversion to dict if row_factory provides sqlite3.Row
        logger.warning(f"No call found with Asterisk UUID: {asterisk_uuid}")
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error in get_call_by_asterisk_uuid for Asterisk UUID {asterisk_uuid}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

def get_call_by_id(call_id: int) -> Optional[Call]:
    """Retrieves a specific call attempt by its ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
        row = cursor.fetchone()
        if row:
            return Call(**dict(row))
        logger.warning(f"No call found with ID: {call_id}")
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error in get_call_by_id for call ID {call_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

# NEW ASYNC FUNCTION
# In database/db_manager.py

def create_call_attempt(call_data: CallCreate) -> Optional[Call]:
    """Creates a new call attempt record in the database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        current_time = datetime.now() # Get current time
        cursor.execute("""
            INSERT INTO calls (task_id, attempt_number, status, prompt_used, scheduled_time)
            VALUES (?, ?, ?, ?, ?)
        """, (call_data.task_id, call_data.attempt_number, call_data.status.value, call_data.prompt_used, current_time)) # Add current_time
        conn.commit()
        call_id = cursor.lastrowid
        if call_id:
            cursor.execute("SELECT * FROM calls WHERE id = ?", (call_id,))
            row = cursor.fetchone()
            return Call(**dict(row)) if row else None
        logger.error(f"Failed to get lastrowid after inserting call for task {call_data.task_id}")
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error in create_call_attempt for task {call_data.task_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

# NEW ASYNC FUNCTION
def update_call_status(call_id: int, status: CallStatus,
                             hangup_cause: Optional[str] = None,
                             call_conclusion: Optional[str] = None,
                             duration_seconds: Optional[int] = None,
                             asterisk_channel: Optional[str] = None,
                             call_uuid: Optional[str] = None) -> bool:
    """Updates the status and other details of a specific call attempt."""
    # This remains internally synchronous for now.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        fields_to_update = ["status = ?"]
        params: List[Any] = [status.value]

        if hangup_cause is not None: fields_to_update.append("hangup_cause = ?"); params.append(hangup_cause)
        if call_conclusion is not None: fields_to_update.append("call_conclusion = ?"); params.append(call_conclusion)
        if duration_seconds is not None: fields_to_update.append("duration_seconds = ?"); params.append(duration_seconds)
        if asterisk_channel is not None: fields_to_update.append("asterisk_channel = ?"); params.append(asterisk_channel)
        if call_uuid is not None: fields_to_update.append("call_uuid = ?"); params.append(call_uuid)
        params.append(call_id)

        query = f"UPDATE calls SET {', '.join(fields_to_update)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        conn.commit()
        updated_rows = cursor.rowcount
        if updated_rows > 0:
            logger.debug(f"Successfully updated call ID {call_id} to status {status.value}")
            return True
        logger.warning(f"No rows updated for call ID {call_id} trying to set status {status.value}")
        return False
    except sqlite3.Error as e:
        logger.error(f"Database error in update_call_status for call ID {call_id}: {e}", exc_info=True)
        return False
    finally:
        conn.close()

def get_calls_for_task(task_id: int) -> List[Call]:
    """Retrieves all call attempts associated with a given task ID."""
    conn = get_db_connection()
    calls = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM calls WHERE task_id = ? ORDER BY attempt_number ASC", (task_id,))
        for row in cursor.fetchall():
            calls.append(Call(**dict(row)))
    except sqlite3.Error as e:
        logger.error(f"Database error fetching calls for task ID {task_id}: {e}", exc_info=True)
    finally:
        conn.close()
    return calls

# --- DND Operations ---
def add_to_dnd_list(dnd_entry_data: DNDEntryCreate) -> Optional[DNDEntry]:
    """Adds a phone number to the DND list for a specific user."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dnd_list (user_id, phone_number, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, phone_number) DO UPDATE SET
            reason = excluded.reason,
            added_at = CURRENT_TIMESTAMP
        """, (dnd_entry_data.user_id, dnd_entry_data.phone_number, dnd_entry_data.reason))
        conn.commit()
        
        # To get the ID (whether inserted or updated), we need to query back
        cursor.execute("SELECT * FROM dnd_list WHERE user_id = ? AND phone_number = ?",
                       (dnd_entry_data.user_id, dnd_entry_data.phone_number))
        row = cursor.fetchone()
        return DNDEntry(**dict(row)) if row else None
        
    except sqlite3.Error as e:
        logger.error(f"Database error adding {dnd_entry_data.phone_number} to DND for user {dnd_entry_data.user_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

def is_on_dnd_list(phone_number: str, user_id: int) -> bool:
    """Checks if a phone number is on the DND list for a specific user."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM dnd_list WHERE phone_number = ? AND user_id = ?", (phone_number, user_id))
        return cursor.fetchone() is not None
    except sqlite3.Error as e:
        logger.error(f"Database error checking DND for {phone_number}, user {user_id}: {e}", exc_info=True)
        return False # Fail safe: assume not on DND if DB error
    finally:
        conn.close()

# --- Call Transcript Operations ---
def log_transcript_entry(entry_data: CallTranscriptCreate) -> Optional[CallTranscript]:
    """Logs a single transcript entry for a call."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO call_transcripts (call_id, speaker, message)
            VALUES (?, ?, ?)
        """, (entry_data.call_id, entry_data.speaker, entry_data.message))
        conn.commit()
        entry_id = cursor.lastrowid
        if entry_id:
            cursor.execute("SELECT * FROM call_transcripts WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return CallTranscript(**dict(row)) if row else None
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error logging transcript for call ID {entry_data.call_id}: {e}", exc_info=True)
        return None
    finally:
        conn.close()

# --- MAIN TEST BLOCK (Illustrative, uses synchronous functions for setup) ---
if __name__ == "__main__": # pragma: no cover
    initialize_database()
    logger.info("\n--- Testing db_manager with campaign workflow (synchronous setup) ---")

    test_user = get_or_create_user(username="db_test_user")
    if not test_user:
        logger.error("Failed to get/create test user for db_manager test.")
        sys.exit(1)
    
    logger.info(f"Using user: {test_user.id} - {test_user.username}")

    campaign_data = CampaignCreate(
        user_id=test_user.id,
        batch_id=str(uuid.uuid4()),
        user_goal_description="DB Manager Test Campaign Goal"
    )
    test_campaign = create_campaign(campaign_data)
    if not test_campaign:
        logger.error("Failed to create test campaign for db_manager test.")
        sys.exit(1)
    logger.info(f"Created campaign: {test_campaign.id}")

    task_c_data = TaskCreate(
        campaign_id=test_campaign.id,
        user_id=test_user.id,
        user_task_description="DB Manager Test Task",
        generated_agent_prompt="Test prompt for db_manager",
        phone_number="+15551234567",
        initial_schedule_time=datetime.now(),
        next_action_time=datetime.now(),
        max_attempts=2 # Override default for testing
    )
    task_id = create_task(task_c_data)
    if not task_id:
        logger.error("Failed to create test task for db_manager test.")
        sys.exit(1)
    logger.info(f"Created task: {task_id}")
    
    retrieved_task = get_task_by_id(task_id)
    if retrieved_task:
        logger.info(f"Retrieved task {retrieved_task.id} with status {retrieved_task.status.value}")

        # Test the new async functions (need an event loop to run them)
        import asyncio
        async def run_async_tests():
            logger.info("\n--- Testing ASYNC db_manager functions ---")
            if retrieved_task:
                # Test create_call_attempt
                call_create_obj = CallCreate(
                    task_id=retrieved_task.id,
                    attempt_number=1,
                    status=CallStatus.PENDING_ORIGINATION,
                    prompt_used=retrieved_task.generated_agent_prompt
                )
                logger.info(f"Attempting to create call for task {retrieved_task.id}")
                created_call = await create_call_attempt(call_create_obj)
                if created_call and created_call.id:
                    logger.info(f"SUCCESS: Async create_call_attempt created call ID: {created_call.id} with status {created_call.status.value}")

                    # Test update_call_status
                    logger.info(f"Attempting to update call {created_call.id} to DIALING")
                    update_success = await update_call_status(
                        call_id=created_call.id,
                        status=CallStatus.DIALING,
                        asterisk_channel="PJSIP/test-00000001"
                    )
                    logger.info(f"SUCCESS: Async update_call_status for call ID {created_call.id}: {update_success}")
                    
                    # Verify update
                    updated_call_record = get_calls_for_task(retrieved_task.id) # get_calls is sync
                    if updated_call_record and updated_call_record[0].status == CallStatus.DIALING:
                         logger.info(f"VERIFIED: Call {created_call.id} status is now {updated_call_record[0].status.value}")
                    else:
                         logger.error(f"FAILED VERIFICATION: Call {created_call.id} status did not update as expected.")

                else:
                    logger.error("FAILED: Async create_call_attempt did not return a valid call object.")
            
            # Test DND
            dnd_entry_data = DNDEntryCreate(user_id=test_user.id, phone_number="+15559876543", reason="Test DND")
            dnd_added = add_to_dnd_list(dnd_entry_data) # Sync
            if dnd_added:
                logger.info(f"Added to DND: {dnd_added.phone_number}")
                is_dnd = is_on_dnd_list(phone_number="+15559876543", user_id=test_user.id) # Sync
                logger.info(f"Is +15559876543 on DND for user {test_user.id}? {is_dnd}")
            
            # Test get_due_tasks with user_id
            due_user_tasks = get_due_tasks(user_id=test_user.id) # Sync
            logger.info(f"Due tasks for user {test_user.id}: {len(due_user_tasks)}")
            if due_user_tasks:
                 logger.info(f"First due task for user {test_user.id}: ID {due_user_tasks[0].id}, Status {due_user_tasks[0].status.value}")


        asyncio.run(run_async_tests())
    else:
        logger.error(f"Could not retrieve task {task_id} for async tests.")


# Add this function to database/db_manager.py

