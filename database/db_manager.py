import sqlite3
import json
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
import sys
from pathlib import Path
import uuid # For generating unique batch IDs

# Add the project root to the Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.app_config import app_config
from database.models import (
    Task, TaskCreate, Call, CallCreate, CallTranscript, CallTranscriptCreate,
    CallEvent, CallEventCreate, DNDEntry, DNDEntryCreate, User, UserCreate,
    Campaign, CampaignCreate
)

# Use a different DB for testing if needed, but for now, the main one is fine.
# We will delete it each time we change the schema.
DATABASE_FILE = app_config.DATABASE_URL.split("sqlite:///./")[-1]
if "pytest" in sys.modules:
     DATABASE_FILE = "test_" + DATABASE_FILE
print(f"Using database file: {DATABASE_FILE}")


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
        print("Database initialized/verified successfully.")
    except FileNotFoundError:
        print(f"Error: schema.sql not found at {schema_path}. Database not initialized.")
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()

# --- User Operations (NEW) ---
def get_or_create_user(username: str) -> Optional[User]:
    """Retrieves a user by username, creating them if they don't exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # First, try to get the user
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return User(**row)

        # If not found, create the user
        cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
        conn.commit()
        user_id = cursor.lastrowid

        # Fetch the newly created user to return the full object
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        new_row = cursor.fetchone()
        return User(**new_row) if new_row else None
    except sqlite3.Error as e:
        print(f"Database error in get_or_create_user: {e}")
        return None
    finally:
        conn.close()

# --- Campaign Operations (NEW) ---
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

        cursor.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        row = cursor.fetchone()
        return Campaign(**row) if row else None
    except sqlite3.Error as e:
        print(f"Database error in create_campaign: {e}")
        return None
    finally:
        conn.close()


def create_batch_of_tasks(campaign: Campaign, tasks_data: List[TaskCreate]) -> bool:
    """Creates multiple tasks linked to a single campaign in a transaction."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Start a transaction
        cursor.execute("BEGIN TRANSACTION")

        tasks_to_insert = []
        for task_data in tasks_data:
            tasks_to_insert.append((
                campaign.id,
                task_data.user_task_description,
                task_data.generated_agent_prompt,
                task_data.phone_number,
                task_data.initial_schedule_time,
                task_data.business_name,
                task_data.person_name,
                task_data.status,
                task_data.next_action_time,
                task_data.max_attempts,
                task_data.current_attempt_count
            ))

        cursor.executemany("""
            INSERT INTO tasks (campaign_id, user_task_description, generated_agent_prompt,
                               phone_number, initial_schedule_time, business_name, person_name,
                               status, next_action_time, max_attempts, current_attempt_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, tasks_to_insert)

        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error in create_batch_of_tasks, rolling back transaction: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# --- Task Operations (Updated to be simpler, single task creation) ---
def create_task(task_data: TaskCreate) -> Optional[int]:
    """Creates a single task. It's better to use create_batch_of_tasks for campaigns."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (campaign_id, user_task_description, generated_agent_prompt,
                               phone_number, initial_schedule_time, business_name, person_name,
                               status, next_action_time, max_attempts, current_attempt_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task_data.campaign_id, task_data.user_task_description, task_data.generated_agent_prompt,
              task_data.phone_number, task_data.initial_schedule_time,
              task_data.business_name, task_data.person_name, task_data.status,
              task_data.next_action_time, task_data.max_attempts, task_data.current_attempt_count))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error in create_task: {e}")
        return None
    finally:
        conn.close()


# --- Other functions (get_task_by_id, update_task_status, etc.) remain largely the same ---
# --- For brevity, I will omit the unchanged functions but they are still part of the file. ---

def get_task_by_id(task_id: int) -> Optional[Task]:
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
    conn = get_db_connection()
    tasks = []
    try:
        cursor = conn.cursor()
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


# --- MAIN TEST BLOCK (REWRITTEN) ---
if __name__ == "__main__":
    initialize_database()

    print("\n--- Testing db_manager with new campaign workflow ---")

    # 1. Get or create a user
    test_username = "APPU"
    user = get_or_create_user(test_username)
    if user:
        print(f"Successfully got or created user: ID={user.id}, Username={user.username}")
    else:
        print(f"Failed to get or create user.")
        sys.exit(1) # Exit if user creation fails

    # 2. Create a campaign for this user
    campaign_goal = "Invite friends to my birthday party on June 16th."
    batch_id = str(uuid.uuid4())
    campaign_data = CampaignCreate(
        user_id=user.id,
        batch_id=batch_id,
        user_goal_description=campaign_goal
    )
    campaign = create_campaign(campaign_data)
    if campaign:
        print(f"Successfully created campaign: ID={campaign.id}, BatchID={campaign.batch_id}")
    else:
        print(f"Failed to create campaign.")
        sys.exit(1)

    # 3. Prepare a batch of tasks for this campaign
    now = datetime.now()
    task1_data = TaskCreate(
        campaign_id=campaign.id,
        user_task_description=campaign_goal,
        generated_agent_prompt="Hello Jhon 1, you're invited...",
        phone_number="+919744554079",
        person_name="Jhon 1",
        initial_schedule_time=now,
        next_action_time=now
    )
    task2_data = TaskCreate(
        campaign_id=campaign.id,
        user_task_description=campaign_goal,
        generated_agent_prompt="Hello Jhon 2, you're invited...",
        phone_number="+919744554080",
        person_name="Jhon 2",
        initial_schedule_time=now,
        next_action_time=now
    )
    batch_tasks = [task1_data, task2_data]

    # 4. Create the batch of tasks in the database
    batch_success = create_batch_of_tasks(campaign, batch_tasks)
    if batch_success:
        print(f"Successfully created a batch of {len(batch_tasks)} tasks for campaign {campaign.id}.")
    else:
        print(f"Failed to create batch of tasks.")

    # 5. Verify by fetching a task
    # To do this properly, we need to know a task ID. For this test, let's assume the first task is ID 1.
    # A better test would query tasks by campaign_id.
    retrieved_task = get_task_by_id(1) # Assuming it's the first task
    if retrieved_task:
        print(f"Verified retrieval of task 1: CampaignID={retrieved_task.campaign_id}, Phone={retrieved_task.phone_number}")
        if retrieved_task.campaign_id == campaign.id:
            print("Verification SUCCESS: Task is correctly linked to the campaign.")
        else:
            print("Verification FAILED: Task is not linked to the correct campaign.")
    else:
        print("Could not retrieve task 1 for verification.")