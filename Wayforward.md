OpenDeep - Master Project State & Forward Plan
1. META-INSTRUCTIONS: HOW TO USE THIS DOCUMENT

(Your Role as the AI Assistant)
Your primary directive is the maintenance and evolution of this Wayforward.md document. This file is the absolute single source of truth for the entire OpenDeep project. It serves as your complete memory and context. Your goal is to ensure it is always perfectly up-to-date, integrating every decision, code change, and architectural agreement we make.

(Your Core Task: The Update-Generate Loop)
When I, the user, ask you to "update the Wayforward file," you must perform the following actions in order:

Ingest Context: Read and fully comprehend two sources of information:

This ENTIRE Wayforward.md document (from version 1.0 to the current state).

The complete, verbatim transcript of our current chat session (the conversation that has occurred since this version of the file was created).

Synthesize & Integrate: Merge the new information from our conversation into the existing structure of this document. This means updating changelogs, file statuses, architectural notes, and the action plan.

Generate a New Version: Your final output for the request must be a single, complete, new Wayforward.md file. This new file is not a diff or a summary; it is the next authoritative version of this document.

(AUTOMATIC PROGRESS TRACKING - CRITICAL)
**MANDATORY ACTION FOR ALL DEVELOPMENT SESSIONS:** At the start of ANY development, implementation, or code modification request, you MUST automatically perform this progress tracking sequence:

1. **Read Progress State**: Always read `IMPLEMENTATION_PROGRESS.md` to understand current progress
2. **Check File Changes**: Review what files have been modified since last update
3. **Update Progress**: Mark completed tasks in `IMPLEMENTATION_PROGRESS.md` based on evidence of completion
4. **Update File Status**: Modify `CODEBASE_MAP.md` file statuses from [Planned] → [In Progress] → [Completed]
5. **Log Achievements**: Update Wayforward.md changelog if significant milestones reached

**Trigger Conditions**: This automatic tracking applies when the user requests:
- Any file modifications, creation, or code changes
- Implementation of specific features or phases
- Testing or debugging activities
- Architecture or design modifications
- Any mention of tasks from `IMPLEMENTATION_PROGRESS.md`

**No User Prompt Needed**: You must perform this tracking automatically without being asked. The user should never need to say "update progress" - you detect and manage this automatically.

(Strict Rules for Regeneration - CRITICAL)

RECURSION: You MUST copy this entire Section 1: META-INSTRUCTIONS verbatim into the new version you generate. This ensures your successor AI instance understands its role perfectly.

INCREMENT VERSION: The first change you make must be to increment the Version number in Section 2.1.

PRESERVE HISTORY (Changelog): The Changelog is an immutable, running log. Never remove old entries. Add a new entry under the new version number detailing the accomplishments of the latest session.

MAINTAIN STABILITY (User Instruction): Do not change variable names, database names, or any other fixed components. New additions are fine, but do not alter existing structures in a way that breaks the established flow.

UPDATE FILE STATUS: In Section 3.2, change the status of files we've worked on from [Planned] to [Created] or [Modified]. Add a concise, one-line summary of each file's purpose if it's new or significantly changed.

INTEGRATE DECISIONS: Architectural agreements and key decisions from our chat must be woven into Section 2.3. Explain why a decision was made, not just what it was.

DEFINE NEXT STEPS: Section 4 must always contain a clear, actionable, and specific plan for what we will do in the very next session.

2. PROJECT OVERVIEW & CURRENT STATE
2.1. Version & Status

Project Version: 18.1

Project Goal: To build a robust, multi-tenant, AI-powered outbound calling system featuring a conversational UI for task definition, an orchestrator for scheduling, a real-time voice AI for calls, an analysis AI for outcomes, and a strategic lifecycle manager for all tasks.

Current Development Phase: Phase 4 (Advanced Task Lifecycle & UI Implementation).

Current Focus: Implementing a full-circle, event-driven task lifecycle, including Human-in-the-Loop (HITL) capabilities, automatic call retries via a new Post Call Analyzer service, and a comprehensive Task Monitoring Dashboard. This phase also absorbs the previously planned function-calling implementation.

Next Major Architectural Step: Build the `PostCallAnalyzerService` to enable automatic retries, implement the HITL flow within the `OrchestratorService`, and develop the new UI dashboard for complete task visibility.

2.2. Changelog / Revision History

v18.1 (Current Version):

Progress Tracking System Implementation:
- Created comprehensive `IMPLEMENTATION_PROGRESS.md` with detailed phase-by-phase task breakdown, estimated effort, dependencies, and completion criteria.
- Established multi-layered tracking approach combining detailed progress tracker + Wayforward.md updates + CODEBASE_MAP.md status updates.
- Defined clear completion criteria for each phase and implementation guidelines including code quality standards, testing requirements, and risk mitigation strategies.
- Ready to begin Phase 1 implementation following structured tracking methodology.

v18.0:

Major Feature Planning: Human-in-the-Loop (HITL) & Task Monitoring Dashboard.
- Defined a comprehensive architecture for a HITL feature, allowing the AI agent to request real-time information from the task creator via the web UI during a live call.
- Designed a task-centric monitoring dashboard to provide a historical view of a task's entire lifecycle, including all associated calls and events.

Deep Codebase Analysis: Task Model & Retry Mechanism.
- Confirmed the existence of a robust, well-defined "Task" entity in the database schema and data models, validating the core architecture.
- Identified that the lack of automatic call retries is due to the `PostCallAnalyzerService` being planned but not yet implemented. The database schema (`max_attempts`, `current_attempt_count`) already supports this feature.

Unified Action Plan:
- Consolidated the previously separate plan for function calling (`end_call`, `send_dtmf`) with the new features into a single, cohesive implementation roadmap.
- The new plan prioritizes building the foundational `PostCallAnalyzerService` first, followed by the HITL feature and the monitoring dashboard.

v17.0:
- Critical Analysis: Function Calling Infrastructure Gap Identified.
- Comprehensive system analysis revealed that while function definitions exist in prompts (end_call, send_dtmf, reschedule_call) and Redis command infrastructure is present, the OpenAI Realtime client lacks function calling implementation.
- Architecture Decision: Implement complete function calling pipeline: AI decision → OpenAI function call → Redis command → AudioSocketHandler execution → Database update → Session cleanup.

(Older versions summarized for brevity)

2.3. Core Architecture & Key Decisions

Progress Tracking Architecture (Decision from v18.1):
- Established a three-tier tracking system for managing complex multi-phase implementation:
  1. **Detailed Progress Tracker** (`IMPLEMENTATION_PROGRESS.md`): Phase-by-phase task breakdown with effort estimates, dependencies, and status tracking
  2. **Master Documentation** (`Wayforward.md`): High-level progress in changelog and version updates
  3. **File Status Tracking** (`CODEBASE_MAP.md`): Real-time file modification status and implementation state
- This approach ensures accountability, enables efficient task resumption after interruptions, and provides clear visibility into project progress.

Task Lifecycle Management (Decision from v18.0):
- The system will be explicitly task-centric. A single "Task" can have multiple call attempts.
- Post Call Analysis: A new `PostCallAnalyzerService` will be created. Its responsibility is to process the outcome of every call. Based on the outcome (e.g., `FAILED_NO_ANSWER`) and the task's configuration (`max_attempts`), it will schedule a retry by updating the task's status to `RETRY_SCHEDULED` and setting a `next_action_time`.
- Human-in-the-Loop (HITL) Flow:
    - The AI agent can autonomously trigger a `request_user_info(question, timeout_seconds, recipient_message)` function call.
    - AI immediately informs call recipient: "I need to ask my task creator for some information to continue, give me a few seconds..."
    - **User Response Within Timeout (≤ timeout_seconds, default 10):** Task creator provides information → Information injected back into live call context → **Call continues seamlessly** (call recipient has been waiting)
    - **User No Response (> timeout_seconds):** Task creator doesn't respond within timeout → AI inserts system message into OpenAI Realtime for graceful call termination → AI tells call recipient "I didn't receive the information I need, I'll call you back once I have it" → **Call ends gracefully** → Task status updates to `PENDING_USER_INFO` → **When task creator finally provides information → NEW CALL is placed to the original call recipient to complete the task with the provided information**
    - The Orchestrator manages user timeout coordination and post-timeout task rescheduling via WebSocket communication with the web UI.
- Auditing: A new `task_events` table will be added to the database to provide a detailed, human-readable log of each task's journey.

Task Monitoring Dashboard (Decision from v18.0):
- A new UI will be created at `/dashboard`.
- The UI will display a list of all Tasks. Each task will be expandable to show its complete history, including all call attempts and all task-level events (e.g., "User Info Requested," "Retry Scheduled").

Function Calling Architecture (Decision from v17.0, to be integrated with v18.0 plan):
- The AI agent's ability to `end_call`, `send_dtmf`, and `reschedule_call` will be implemented. These actions will now feed into the new Task Lifecycle Management system. For example, `reschedule_call` will be handled by the `PostCallAnalyzerService` or `OrchestratorService` to update the task state correctly.

(Other core decisions regarding AudioSocket, UUIDs, and AMI remain as in previous versions).

3. IMPLEMENTATION & FILE MANIFEST
3.1. Required Libraries
- fastapi, uvicorn, sqlalchemy, redis, openai, python-dotenv, pydantic, google-generativeai, httpx, asterisk-ami==0.1.7, uuid, numpy

3.2. Detailed File Structure & Status

Project Management & Documentation:
- `IMPLEMENTATION_PROGRESS.md` [Created] - Comprehensive phase-by-phase progress tracker with task breakdown, effort estimates, dependencies, and completion criteria.
- `Wayforward.md` [Modified] - Updated with progress tracking system and version 18.1 changelog.
- `CODEBASE_MAP.md` [To Be Modified] - Will be updated with implementation status as files are modified.

Core Task & Call Lifecycle (Focus for Current Phase):
- `post_call_analyzer_service/analysis_svc.py` [To Be Created] - Core logic for call retries and post-call processing.
- `task_manager/orchestrator_svc.py` [To Be Modified] - Will be enhanced with HITL state management logic.
- `task_manager/task_scheduler_svc.py` [To Be Modified] - Will read from `RETRY_SCHEDULED` status to initiate new call attempts.
- `database/schema.sql` [To Be Modified] - Add the new `task_events` table.
- `database/models.py` [To Be Modified] - Add Pydantic models for `TaskEvent`.
- `audio_processing_service/openai_realtime_client.py` [To Be Modified] - Implement the `request_user_info` function definition and tool configuration.

Web Interface (Focus for Current Phase):
- `web_interface/app.py` [To Be Modified] - Add WebSocket endpoints for HITL communication.
- `web_interface/routes_api.py` [To Be Modified] - Add new API endpoint to fetch detailed task data for the dashboard.
- `web_interface/routes_ui.py` [To Be Modified] - Add the `/dashboard` route.
- `web_interface/templates/dashboard.html` [To Be Created] - The HTML structure for the new monitoring dashboard.
- `web_interface/static/js/dashboard.js` [To Be Created] - Frontend logic for the dashboard.
- `web_interface/static/js/main.js` [To Be Modified] - Will be updated to handle the WebSocket messages for the HITL feature.

(Other files remain as per their status in v17.0 unless modified as part of the implementation).

4. IMMEDIATE NEXT STEPS (ACTION PLAN)

With the comprehensive progress tracking system now in place, development will proceed following the structured approach outlined in `IMPLEMENTATION_PROGRESS.md`.

**NEXT SESSION PRIORITY: Begin Phase 1 Implementation**

1. **Database Schema Updates** (Start with Task 1.1):
   - Modify `database/schema.sql` to add the `task_events` table
   - Define proper table structure with foreign keys to tasks table
   - Add performance indexes for common queries
   - Test schema changes with existing data

2. **Progress Tracking Protocol** (Establish working rhythm):
   - Update `IMPLEMENTATION_PROGRESS.md` task status after each completion
   - Update `CODEBASE_MAP.md` file status as files are modified
   - Regular Wayforward.md changelog updates at major milestones

3. **Development Environment Preparation**:
   - Verify database backup procedures before schema changes
   - Ensure test environment availability for validation
   - Set up proper logging for new services

The detailed breakdown of all subsequent tasks, dependencies, and estimated effort is available in `IMPLEMENTATION_PROGRESS.md`. This structured approach ensures efficient progress tracking and enables seamless task resumption after any interruptions.