-- Main tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_task_description TEXT NOT NULL,            -- What the user initially typed
    generated_agent_prompt TEXT NOT NULL,           -- Prompt for the Realtime Call LLM
    business_name TEXT,
    person_name TEXT,
    phone_number TEXT NOT NULL,
    status TEXT DEFAULT 'pending',                  -- pending, in-progress, completed, failed_conclusive, on_hold, pending_analysis
    overall_conclusion TEXT,                      -- Final summary after all attempts for this task
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- Auto-update this with a trigger if db supports, else app logic
    next_action_time DATETIME,                      -- When the next step for this task should be considered
    max_attempts INTEGER DEFAULT 3,
    current_attempt_count INTEGER DEFAULT 0,
    initial_schedule_time DATETIME NOT NULL        -- When the user originally scheduled this task
);

-- Individual call attempts for each task
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,                       -- Foreign key to tasks table
    attempt_number INTEGER NOT NULL,
    scheduled_time DATETIME NOT NULL,               -- Actual time this attempt was scheduled/initiated
    status TEXT DEFAULT 'pending',                  -- pending_initiation, dialing, in-progress, completed_attempt, failed_attempt, rescheduled_by_agent
    asterisk_channel TEXT,                          -- e.g., PJSIP/opendeep-00000001
    call_uuid TEXT UNIQUE,                          -- UUID for this specific call attempt from Asterisk/AudioSocket
    prompt_used TEXT,                               -- The specific prompt used for this attempt
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    call_conclusion TEXT,                           -- Summary of this specific call attempt's outcome
    hangup_cause TEXT,                              -- Hangup cause from Asterisk if available
    duration_seconds INTEGER,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Transcripts for each call attempt
CREATE TABLE IF NOT EXISTS call_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,                       -- Foreign key to calls table (specific attempt)
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    speaker TEXT NOT NULL CHECK(speaker IN ('user', 'agent', 'system')), -- 'user' (callee), 'agent' (OpenAI Realtime), 'system' (internal notes)
    message TEXT NOT NULL,
    FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
);

-- Events happening during a call attempt
CREATE TABLE IF NOT EXISTS call_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,                       -- Foreign key to calls table
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT NOT NULL,                       -- 'dtmf_sent', 'call_started', 'call_ended_by_agent', 'call_ended_by_user', 'function_called_by_agent', 'error'
    details TEXT,                                   -- JSON string with event-specific details
    FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
);

-- Do Not Disturb list
CREATE TABLE IF NOT EXISTS dnd_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT UNIQUE NOT NULL,
    reason TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task_id INTEGER,                                -- Optionally link to task that resulted in DND
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

-- Triggers for updated_at (SQLite specific)
CREATE TRIGGER IF NOT EXISTS tasks_updated_at_trigger
AFTER UPDATE ON tasks
FOR EACH ROW
BEGIN
    UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS calls_updated_at_trigger
AFTER UPDATE ON calls
FOR EACH ROW
BEGIN
    UPDATE calls SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_tasks_status_next_action_time ON tasks (status, next_action_time);
CREATE INDEX IF NOT EXISTS idx_calls_task_id ON calls (task_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls (status);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_call_id ON call_transcripts (call_id);
CREATE INDEX IF NOT EXISTS idx_call_events_call_id ON call_events (call_id);
CREATE INDEX IF NOT EXISTS idx_dnd_list_phone_number ON dnd_list (phone_number);