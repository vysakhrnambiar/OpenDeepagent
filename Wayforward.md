# OpenDeep - Master Project State & Forward Plan

## 1. META-INSTRUCTIONS: HOW TO USE THIS DOCUMENT

(Your Role as the AI Assistant)
Your primary directive is the maintenance and evolution of this Wayforward.md document. This file is the absolute single source of truth for the entire OpenDeep project. It serves as your complete memory and context. Your goal is to ensure it is always perfectly up-to-date, integrating every decision, code change, and architectural agreement we make.

(Your Core Task: The Update-Generate Loop)
When I, the user, ask you to "update the Wayforward file," you must perform the following actions in order:

Ingest Context: Read and fully comprehend two sources of information:
- This ENTIRE Wayforward.md document (from version 1.0 to the current state).
- The complete, verbatim transcript of our current chat session (the conversation that has occurred since this version of the file was created).

Synthesize & Integrate: Merge the new information from our conversation into the existing structure of this document. This means updating changelogs, file statuses, architectural notes, and the action plan.

Generate a New Version: Your final output for the request must be a single, complete, new Wayforward.md file. This new file is not a diff or a summary; it is the next authoritative version of this document.

(Strict Rules for Regeneration - CRITICAL)
- RECURSION: You MUST copy this entire Section 1: META-INSTRUCTIONS verbatim into the new version you generate. This ensures your successor AI instance understands its role perfectly.
- INCREMENT VERSION: The first change you make must be to increment the Version number in Section 2.1.
- PRESERVE HISTORY (Changelog): The Changelog is an immutable, running log. Never remove old entries. Add a new entry under the new version number detailing the accomplishments of the latest session.
- UPDATE FILE STATUS: In Section 3.2, change the status of files we've worked on from [Planned] to [Created] or [Modified]. Add a concise, one-line summary of each file's purpose if it's new or significantly changed.
- INTEGRATE DECISIONS: Architectural agreements and key decisions from our chat must be woven into Section 2.3. Explain why a decision was made, not just what it was.
- DEFINE NEXT STEPS: Section 4 must always contain a clear, actionable, and specific plan for what we will do in the very next session.

## 2. PROJECT OVERVIEW & CURRENT STATE

### 2.1. Version & Status

Project Version: 6.0

Project Goal: To build a robust, multi-tenant, AI-powered outbound calling system featuring a conversational UI for task definition, an orchestrator for scheduling, a real-time voice AI for calls, and an analysis AI for outcomes, with capabilities for Human-in-the-Loop (HITL) feedback.

Current Development Phase:
- Phase 1 (UI Foundation): Complete.
- Phase 1.5 (Fixes & Search Tool): Complete.
- Phase 1.6 (Authoritative Business Search & API Integration): Complete.
- Phase 2a (LLM Campaign Orchestration - UI Button & Backend Service): Complete.
- Next: Phase 2b (Task Execution Engine - Task Scheduler Service).

### 2.2. Changelog / Revision History

v6.0 (Current Version):
- Feature Complete (Phase 2a): Successfully implemented the OrchestratorService and the /api/execute_campaign endpoint. The "Confirm and Schedule Campaign" button in the UI now successfully triggers the creation of campaign and task records in the database.
- Fix: Resolved TypeError in UIAssistantService.__init__() by updating it to correctly accept username.
- Fix: Resolved AttributeError in OpenAIFormClient by implementing generate_json_completion_with_tools.
- Fix: Resolved NameError in openai_form_client.py by adding import asyncio.
- Fix: Resolved AttributeError in GoogleGeminiClient by correcting get_authoritative_business_info to use direct HTTP calls.
- Fix: Corrected return type handling in OrchestratorService.execute_plan for proper UI response.
- Architecture: Detailed planning for Human-in-the-Loop (HITL) feedback mechanism.
- Architecture: Clarified distinct roles of Orchestrator, Scheduler, and Post-Call Analyzer.
- Future Vision: Planned for richer user profiles to enhance AI contextual awareness.

v5.0:
- Fix: Resolved persistent 403 Forbidden errors from Google Cloud APIs by consolidating to a single project.
- Upgrade: Migrated from legacy googlemaps library to modern Places API (New) via direct HTTP requests.
- Enhancement: Added "Feedback Loop" instruction to UI_ASSISTANT_SYSTEM_PROMPT.
- Enhancement: Implemented _is_plan_valid validation check in UIAssistantService.

v4.0:
- Feature: Implemented get_authoritative_business_info tool using Google Places API.
- Refactor: Updated UIAssistantService to support multiple tools.
- Dependency: Added googlemaps library (later replaced).

v3.0:
- Meta: Re-architected this Wayforward.md file to be a comprehensive, self-sustaining context document.

v2.0:
- Feature: Implemented generic search_internet tool for broader information queries.
- Refactor: Updated ui_assistant_svc.py with robust tool-calling loop.
- Refactor: Created information_retriever_svc.py and google_gemini_client.py.
- Fix: Resolved ModuleNotFoundError for google.generativeai with correct pip install.

v1.0:
- Fix: Corrected non-functional "Submit Answers" button in frontend form.
- Fix: Resolved username editing issue, allowing editing until first message is sent.
- Fix: Corrected CSS/HTML layout issues, restoring single-centered chat window.
- Feature: Built initial conversational UI with default username.
- Core: Established base project structure, database schema, and FastAPI application.

### 2.3. Core Architecture & Key Decisions

Separation of Concerns (Orchestrator, Scheduler, Analyzer):
- Orchestrator (OrchestratorService): A one-time setup agent that takes the UI's final campaign_plan and creates database records. Once complete, its job is done for that plan.
- Scheduler (TaskSchedulerService): A persistent, long-running background process that polls for due tasks and initiates call attempts.
- Post-Call Analyzer (AnalysisService): A decision-making agent that analyzes call outcomes and determines next steps.

Human-in-the-Loop (HITL) Feedback:
- Allows the Live Call AI to request immediate feedback from the UI user during active calls.
- Involves a request_user_feedback tool, WebSocket bridge, feedback_requests table, and enhancements to call handling components.

Multi-Tenancy & User Context:
- Core system uses user_id to associate campaigns and tasks, with username passed from UI to backend.
- Future plans include expanded user profiles to store preferences and reduce repetitive questioning.

Tool-Augmented AI:
- UIAssistantService uses a two-step tool-calling loop for factual queries.
- Specialized tools include get_authoritative_business_info for business data and search_internet for general information.

Modern API Usage:
- Deliberate use of Google Places API (New) via direct HTTP requests for reliability and future-proofing.

Task vs. Call Distinction:
- A Task is the overall objective defined by the user (e.g., "book a dentist appointment").
- A Call is an individual attempt to complete a Task. A single Task may involve multiple Call attempts.
- SQLite schema reflects this with tasks and calls tables.

Resilience and Error Handling:
- Handles unexpected call drops by logging errors and setting tasks for analysis.
- Implements retry logic with backoff for transient issues.
- Gracefully handles service failures across DB, Redis, and OpenAI dependencies.

## 3. IMPLEMENTATION & FILE MANIFEST

### 3.1. Required Libraries
fastapi, uvicorn, sqlalchemy, redis, openai, python-dotenv, pydantic, google-generativeai, httpx.

### 3.2. Detailed File Structure & Status

main.py [Created] - Single entry point for the application.

config/app_config.py [Modified] - Contains all configuration keys including DB, Redis, OpenAI, and GOOGLE_API_KEY.

config/prompt_config.py [Modified] - Defines system prompts with tool usage rules and feedback loops.

database/schema.sql [Created] - Defines tables for users, campaigns, tasks, calls, call_transcripts, call_events, dnd_list.

database/models.py [Modified] - Pydantic models reflecting the database schema.

database/db_manager.py [Modified] - Handles database operations including user creation and task management.

llm_integrations/openai_form_client.py [Modified] - Client for OpenAI with tools support.

llm_integrations/google_gemini_client.py [Created] - Client for Google Gemini with search grounding.

task_manager/ui_assistant_svc.py [Modified] - Implements the tool-calling loop for the chat interface.

task_manager/orchestrator_svc.py [Created] - Creates database records from campaign plans.

tools/information_retriever_svc.py [Modified] - Defines search tools for business and general information.

web_interface/app.py [Modified] - FastAPI application setup.

web_interface/routes_api.py [Modified] - API endpoints for chat and campaign execution.

web_interface/routes_ui.py [Created] - Serves HTML templates.

web_interface/static/css/style.css [Modified] - UI styling.

web_interface/static/js/main.js [Modified] - Frontend logic including campaign confirmation.

web_interface/templates/index.html [Created] - Main chat interface.

common/data_models.py [Modified] - API request/response models.

common/logger_setup.py [Created] - Centralized logging.

common/redis_client.py [Created] - Redis Pub/Sub interface.

[Planned] task_manager/task_scheduler_svc.py - Background service to poll for due tasks.

[Planned] call_processor_service/call_initiator_svc.py - Manages concurrency for calls.

[Planned] call_processor_service/asterisk_ami_client.py - Asterisk Manager Interface.

[Planned] call_processor_service/call_attempt_handler.py - Single call lifecycle management.

[Planned] audio_processing_service/* - Real-time audio handling with Asterisk and OpenAI.

[Planned] post_call_analyzer_service/analysis_svc.py - Call outcome analysis.

[Planned] task_manager/feedback_manager_svc.py - HITL feedback system.

[Planned] campaign_summarizer_service/* - Final report generation.

## 4. IMMEDIATE NEXT STEPS (ACTION PLAN)

Phase 2a (LLM Campaign Orchestration) is now COMPLETE. The UI allows plan creation, and the "Confirm and Schedule Campaign" button successfully triggers database record creation.

The next logical step is to implement Phase 2b: Building the Core Task Execution Engine, starting with the TaskSchedulerService.

Create task_manager/task_scheduler_svc.py:
- This will be a long-running background process.
- It will periodically poll the tasks table for due items (status='pending'/'on_hold', next_action_time in past, attempt_count < max).
- For each due task, it will update the task's status to 'in-progress' and log that it would initiate a call.
- Initially, this will be a standalone service. Later, it will trigger the CallInitiatorService (to be built after the Scheduler).


Okay, I will now update the Wayforward.md document.

I have processed our entire conversation since the last update (Version 6.0). I will integrate all the decisions regarding:

The precise mechanics of call concurrency and the roles of CallInitiatorService, AudioSocketServer/Handler, and CallAttemptHandler.

The introduction and planned functionality of the TaskLifecycleManagerService (our "Meta-OODA" agent), including its LLM-driven nature, its purview over all tasks, dynamic determination of long-running tasks, and proactive user check-ins.

The critical, pervasive principle of multi-tenancy for all new components and LLM interactions, ensuring data isolation.

The DND list handling clarification (confirming it's not what "OODA" referred to, but still important for the scheduler).

Please copy the entire content of my next message, as that will be the new Wayforward.md Version 7.0.

OpenDeep - Master Project State & Forward Plan

META-INSTRUCTIONS: HOW TO USE THIS DOCUMENT

(Your Role as the AI Assistant)
Your primary directive is the maintenance and evolution of this Wayforward.md document. This file is the absolute single source of truth for the entire OpenDeep project. It serves as your complete memory and context. Your goal is to ensure it is always perfectly up-to-date, integrating every decision, code change, and architectural agreement we make.

(Your Core Task: The Update-Generate Loop)
When I, the user, ask you to "update the Wayforward file," you must perform the following actions in order:

Ingest Context: Read and fully comprehend two sources of information:

This ENTIRE Wayforward.md document (from version 1.0 to the current state).

The complete, verbatim transcript of our current chat session (the conversation that has occurred since this version of the file was created).

Synthesize & Integrate: Merge the new information from our conversation into the existing structure of this document. This means updating changelogs, file statuses, architectural notes, and the action plan.

Generate a New Version: Your final output for the request must be a single, complete, new Wayforward.md file. This new file is not a diff or a summary; it is the next authoritative version of this document.

(Strict Rules for Regeneration - CRITICAL)

RECURSION: You MUST copy this entire Section 1: META-INSTRUCTIONS verbatim into the new version you generate. This ensures your successor AI instance understands its role perfectly.

INCREMENT VERSION: The first change you make must be to increment the Version number in Section 2.1.

PRESERVE HISTORY (Changelog): The Changelog is an immutable, running log. Never remove old entries. Add a new entry under the new version number detailing the accomplishments of the latest session.

UPDATE FILE STATUS: In Section 3.2, change the status of files we've worked on from [Planned] to [Created] or [Modified]. Add a concise, one-line summary of each file's purpose if it's new or significantly changed.

INTEGRATE DECISIONS: Architectural agreements and key decisions from our chat must be woven into Section 2.3. Explain why a decision was made, not just what it was.

DEFINE NEXT STEPS: Section 4 must always contain a clear, actionable, and specific plan for what we will do in the very next session.

PROJECT OVERVIEW & CURRENT STATE

2.1. Version & Status

Project Version: 7.0

Project Goal: To build a robust, multi-tenant, AI-powered outbound calling system featuring a conversational UI for task definition, an orchestrator for scheduling, a real-time voice AI for calls, an analysis AI for outcomes, and a strategic lifecycle manager for all tasks, with capabilities for Human-in-the-Loop (HITL) feedback.

Current Development Phase:

Phase 1 (UI Foundation): Complete.
Phase 1.5 (Fixes & Search Tool): Complete.
Phase 1.6 (Authoritative Business Search & API Integration): Complete.
Phase 2a (LLM Campaign Orchestration - UI Button & Backend Service): Complete.
Next: Phase 2b (Task Execution Engine - Task Scheduler Service Implementation).

2.2. Changelog / Revision History

v7.0 (Current Version):

Architecture: Detailed the "Meta-OODA" agent, now named TaskLifecycleManagerService. Confirmed it will be a dedicated, LLM-driven service responsible for strategic oversight of all tasks (short and long-running), dynamic determination of task nature, proactive user check-ins for long tasks, and ensuring no collisions with PostCallAnalyzerService.
Architecture: Solidified the pervasive multi-tenancy design principle. All new services, database queries, and LLM interactions must be strictly scoped by user_id to ensure data isolation.
Architecture: Refined the understanding of call concurrency. CallInitiatorService enforces MAX_CONCURRENT_CALLS by managing how many CallAttemptHandler tasks are active. Each live call will have an AudioSocketHandler (spawned by AudioSocketServer upon Asterisk connection) and a corresponding CallAttemptHandler (managing AMI commands via Redis).
Architecture: Clarified that DND list checks are the responsibility of the TaskSchedulerService (pre-call) and PostCallAnalyzerService (post-call update). "OODA" does not refer to DND.
Planning: Confirmed that task prioritization will be a future enhancement involving database schema changes and logic updates in the TaskSchedulerService and OrchestratorService.

v6.0:

Feature Complete (Phase 2a): Successfully implemented the OrchestratorService and the /api/execute_campaign endpoint. The "Confirm and Schedule Campaign" button now creates campaign and task records.
Fixes: Resolved multiple TypeErrors, AttributeErrors, and NameErrors across UIAssistantService, OpenAIFormClient, and OrchestratorService related to function arguments, method existence, and imports. Corrected logic in get_authoritative_business_info to use direct HTTP calls.
Architecture: Initial detailed planning for Human-in-the-Loop (HITL) feedback. Clarified distinct roles of Orchestrator, Scheduler, and Post-Call Analyzer.

v5.0 - v1.0:
Resolved Google Cloud API issues, upgraded to modern Places API. Enhanced UI prompt with feedback loops and code-level validation. Implemented search tools (general and business-specific). Initial UI bug fixes, multi-tool support, multi-tenancy foundations, and base project structure.

2.3. Core Architecture & Key Decisions

Multi-Tenancy: A non-negotiable, foundational principle.
* Data Isolation: All key tables (users, campaigns, tasks, calls, call_transcripts, dnd_list, etc.) must include user_id. All database operations must be filtered by user_id.
* Service Design: All service methods operating on user-specific data must accept user_id and use it.
* LLM Context: Prompts and data fed to any LLM must be strictly scoped to the current user_id.

Separation of Concerns & Control Flow for Calls:
* TaskSchedulerService: Polls DB for due tasks (respecting user_id). Hands off to CallInitiatorService. Performs DND checks. (Future: will handle priority).
* CallInitiatorService: Gatekeeper for MAX_CONCURRENT_CALLS. If slot available, creates calls record, spawns a CallAttemptHandler task, and instructs AsteriskAmiClient to originate call (passing calls.id as channel variable).
* AudioSocketServer: Listens for Asterisk AudioSocket connections. Spawns an AudioSocketHandler task for each.
* AudioSocketHandler: Manages one live audio stream. Receives calls.id from Asterisk. Instantiates OpenAIRealtimeClient. Bridges audio. Publishes LLM-triggered commands (DTMF, end_call) to Redis channel call_commands:{calls.id}.
* CallAttemptHandler: Manages overall lifecycle for one calls.id. Subscribes to call_commands:{calls.id} on Redis. Uses AsteriskAmiClient to execute these commands. Monitors AMI for call end. Updates calls table.
* PostCallAnalyzerService: Reactive. Analyzes a single completed call attempt. Updates task status (success, fail, retry), suggests next steps, updates DND list if needed. Feeds data for the TaskLifecycleManagerService.
* TaskLifecycleManagerService (Meta-OODA Agent): Proactive & Strategic. LLM-driven.
* Oversees all tasks for all users (respecting user_id scoping).
* Monitors task queues, progress of long-running tasks, identifies stuck tasks.
* Dynamically determines task nature (long/short running).
* Decides on strategic adjustments (dynamic prioritization, prompt revisions, placing tasks on hold).
* Manages inter-call context for long-lived/phased tasks.
* Initiates proactive user check-ins for long-running tasks via UI.
* Acts by updating DB records, influencing the TaskSchedulerService.

Human-in-the-Loop (HITL) Feedback: Planned. Allows Live Call AI to request immediate UI user feedback. Involves new AI tool, WebSocket bridge, feedback_requests DB table, and service enhancements.

Tool-Augmented UI Assistant: UIAssistantService uses a mandatory, two-step tool-calling loop for factual queries (Places API for business, Gemini for general).

Modern API Usage: Google Places API (New) via direct HTTP (httpx).

Task vs. Call: A task is the objective. A call (or call_attempt) is one phone call attempt for that task.

IMPLEMENTATION & FILE MANIFEST

3.1. Required Libraries
fastapi, uvicorn, sqlalchemy, redis, openai, python-dotenv, pydantic, google-generativeai, httpx.

3.2. Detailed File Structure & Status
(Key files and those recently changed/planned next)

main.py [Created] - Single entry point.

config/app_config.py [Modified] - All global configurations.

config/prompt_config.py [Modified] - Prompts for UI Assistant, Orchestrator. (Planned: LIFECYCLE_MANAGER_SYSTEM_PROMPT, REALTIME_CALL_LLM_BASE_INSTRUCTIONS, POST_CALL_ANALYSIS_SYSTEM_PROMPT).

database/schema.sql [Modified] - Defines tables. (Planned: priority in tasks, inter_call_context in tasks, feedback_requests table).

database/models.py [Modified] - Pydantic models for DB tables.

database/db_manager.py [Modified] - All DB operations.

llm_integrations/openai_form_client.py [Modified] - Client for OpenAI.

llm_integrations/google_gemini_client.py [Created] - Client for Gemini.

task_manager/ui_assistant_svc.py [Modified] - UI chat logic.

task_manager/orchestrator_svc.py [Created] - Converts UI plan to DB tasks.

tools/information_retriever_svc.py [Modified] - Defines search tools.

web_interface/app.py [Modified] - FastAPI app setup.

web_interface/routes_api.py [Modified] - /api/chat_interaction, /api/execute_campaign.

web_interface/routes_ui.py [Created] - Serves index.html.

web_interface/static/js/main.js [Modified] - Frontend logic.

common/data_models.py [Modified] - API/Redis Pydantic models.

common/logger_setup.py [Created] - Logging.

common/redis_client.py [Created] - Redis client.

[Planned Next] task_manager/task_scheduler_svc.py - Background service to poll DB for due tasks (user_id scoped) and initiate call chain.

[Planned] call_processor_service/call_initiator_svc.py - Manages MAX_CONCURRENT_CALLS, starts CallAttemptHandler tasks, triggers AMI.

[Planned] call_processor_service/asterisk_ami_client.py - Low-level Asterisk AMI communication.

[Planned] call_processor_service/call_attempt_handler.py - Manages a single call's lifecycle via AMI and Redis commands.

[Planned] audio_processing_service/audio_socket_server.py - Listens for Asterisk AudioSocket connections.

[Planned] audio_processing_service/audio_socket_handler.py - Handles one audio stream, bridges to OpenAIRealtimeClient.

[Planned] audio_processing_service/openai_realtime_client.py - Wrapper for OpenAI Realtime Voice.

[Planned] post_call_analyzer_service/analysis_svc.py - Analyzes call outcomes.

[Planned] task_manager/task_lifecycle_manager_svc.py - The "Meta-OODA" agent for strategic task oversight.

[Planned] task_manager/feedback_manager_svc.py (for HITL).

[Planned] campaign_summarizer_service/* - For final campaign reports.

IMMEDIATE NEXT STEPS (ACTION PLAN)

Phase 2a (LLM Campaign Orchestration - UI Button & Backend Service) is COMPLETE.

The next concrete objective is to implement Phase 2b: Building the Core Task Execution Engine, starting with the TaskSchedulerService.

Create task_manager/task_scheduler_svc.py:

This service will be a long-running asyncio background process.

It will periodically poll the tasks table for tasks ready for action:

Query db_manager.get_due_tasks(user_id=None) (initially, it might poll for all users, or we can design it to manage tasks per user if sharding is ever needed, but for now, user_id filtering in the query is key). The query should consider status IN ('pending', 'on_hold', 'retry_scheduled'), next_action_time <= NOW(), current_attempt_count < max_attempts.

Multi-tenancy: Ensure user_id is fetched with task data.

DND Check: For each due task, it must call db_manager.is_on_dnd_list(phone_number, user_id). If DND, update task status to cancelled_dnd and skip.

Dispatch (Stubbed): For each valid due task, it will (for now) log that it would dispatch this task (including task_id and user_id) to the CallInitiatorService. The actual call to CallInitiatorService will be implemented once that service exists.

Update the task's status to processing_by_scheduler or queued_for_call in the database to prevent re-picking by the same scheduler poll.

Update database/db_manager.py:

Implement get_due_tasks() method with appropriate filtering and ordering (initially by next_action_time).

Implement is_on_dnd_list(phone_number, user_id).

Update main.py:

Instantiate TaskSchedulerService.

Add task_scheduler_svc.run_scheduler_loop() to the asyncio.gather() call in main_services() so it starts with the application.

This will give us the foundational loop that picks up scheduled tasks, respects DND, and is ready to hand them off for actual call initiation in the next step.