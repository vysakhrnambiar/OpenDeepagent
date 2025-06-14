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
- MAINTAIN STABILITY (User Instruction): Do not change variable names, database names, or any other fixed components. New additions are fine, but do not alter existing structures in a way that breaks the established flow.
- UPDATE FILE STATUS: In Section 3.2, change the status of files we've worked on from [Planned] to [Created] or [Modified]. Add a concise, one-line summary of each file's purpose if it's new or significantly changed.
- INTEGRATE DECISIONS: Architectural agreements and key decisions from our chat must be woven into Section 2.3. Explain why a decision was made, not just what it was.
- DEFINE NEXT STEPS: Section 4 must always contain a clear, actionable, and specific plan for what we will do in the very next session.

## 2. PROJECT OVERVIEW & CURRENT STATE

### 2.1. Version & Status

Project Version: 13.0

Project Goal: To build a robust, multi-tenant, AI-powered outbound calling system featuring a conversational UI for task definition, an orchestrator for scheduling, a real-time voice AI for calls, an analysis AI for outcomes, and a strategic lifecycle manager for all tasks.

Current Development Phase: Phase 2b (Task Execution Engine & Audio Path)

Current Focus: Implementing proper UUID handling for Asterisk interactions to resolve the app_audiosocket "Failed to parse UUID" error and achieve a stable WebSocket connection for audio.

Next Major Architectural Step: Establish a stable, bi-directional audio stream between the softphone, Asterisk, and the Python AudioSocketHandler. Then, integrate AI audio processing.

### 2.2. Changelog / Revision History

v13.0 (Current Version):
- Critical Insight: Identified that app_audiosocket.c in Asterisk requires a standard UUID in the URI path
- Root Cause Diagnosis: The AudioSocket application expects a valid UUID when used as AudioSocket(URI,cn)
- Architecture Decision: Will generate UUID (uuid.uuid4()) in CallAttemptHandler for Asterisk's consumption
- Database Modification: Added asterisk_call_uuid (TEXT, UNIQUE) column to calls table
- Code Modifications: Updated multiple components to handle the new UUID system

v12.0:
- Major Success: Resolved call origination issues, achieved working command and control pipeline
- Fixed: Originate command, AMI variable passing, Local channel hangup, PJSIP issues
- Added: AMI event reception verification and logging
- Issue Identified: app_audiosocket.c UUID parsing error

v11.0:
- Success: Fixed task creation and service initialization issues
- Implemented: FastAPI lifespan manager
- Bug Identified: TaskSchedulerService task pickup issue

v10.0:
- Major Success: Refactored AsteriskAmiClient using asterisk-ami library
- Achieved: Reliable AMI connection and login

v9.0:
- Architecture Decision: Centralized AMI client approach
- Clarified: WebSocket and threading architecture

v8.0:
- Feature Started: Initial task_manager/task_scheduler_svc.py structure
- Added: Meta-instruction for fixed component names

v7.0:
- Architecture: Detailed TaskLifecycleManagerService
- Refined: Multi-tenancy and call concurrency understanding

v6.0:
- Feature Complete: OrchestratorService and campaign execution
- Architecture: Initial HITL feedback planning

v5.0:
- Fixed: Google Cloud API issues
- Upgraded: Places API implementation

v4.0:
- Feature: Implemented authoritative business info tool
- Refactor: Enhanced UIAssistantService

v3.0:
- Meta: Restructured Wayforward.md format

v2.0:
- Feature: Added internet search tool
- Refactor: Enhanced tool-calling system

v1.0:
- Initial: Base project structure and UI implementation

### 2.3. Core Architecture & Key Decisions

UUID for Asterisk Interaction (v13.0):
- Generate standard uuid.uuid4() at call origination
- Store in asterisk_call_uuid column (calls table)
- Use in WebSocket URI path for Asterisk
- Map between UUID and internal call_id as needed

Dialplan for AudioSocket (v13.0):
```
[opendeep-audiosocket-outbound]
exten => s,1,NoOp(=== ... ===)
   same => n,Set(ASTERISK_CALL_UUID=${CUT(OPENDDEEP_VARS,|,1)})
   same => n,Set(ACTUAL_TARGET_TO_DIAL=${CUT(OPENDDEEP_VARS,|,2)})
   same => n,Answer()
   same => n,AudioSocket(ws://YOUR_PYTHON_IP:1200/callaudio/${ASTERISK_CALL_UUID},cn)
   same => n,Dial(${ACTUAL_TARGET_TO_DIAL},30,g)
```

AMI Event-Driven Call Progress:
- Asynchronous event handling for call status tracking
- "Fire and forget" Originate actions with Async: true

Multi-Tenancy:
- Data isolation via user_id
- User-specific DND lists
- Scoped LLM contexts

Service Architecture:
- TaskSchedulerService: Polling and DND checks
- CallInitiatorService: Concurrency management
- CallAttemptHandler: Per-call AMI interaction
- AudioSocketServer: Asterisk audio connection
- AudioSocketHandler: Audio stream management
- PostCallAnalyzerService: Outcome analysis
- TaskLifecycleManagerService: Strategic oversight

Development & Testing:
- Test Mode with redirected calls
- Sequential execution option
- Iterative component testing

## 3. IMPLEMENTATION & FILE MANIFEST

### 3.1. Required Libraries
fastapi, uvicorn, sqlalchemy, redis, openai, python-dotenv, pydantic, google-generativeai, httpx, asterisk-ami==0.1.7, uuid

### 3.2. Detailed File Structure & Status

**Core Services:**
- database/schema.sql [Modified] - Database schema with new asterisk_call_uuid column
- database/models.py [Modified] - Pydantic models with UUID support
- database/db_manager.py [Modified] - Database operations including UUID handling
- call_processor_service/call_attempt_handler.py [Modified] - Call handling with UUID generation
- call_processor_service/asterisk_ami_client.py [Modified] - AMI interaction
- audio_processing_service/audio_socket_server.py [Modified] - WebSocket server with UUID parsing
- audio_processing_service/audio_socket_handler.py [Modified] - Audio stream handling

**Supporting Services:**
- task_manager/orchestrator_svc.py [Modified] - Campaign orchestration
- task_manager/task_scheduler_svc.py [Modified] - Task scheduling
- call_processor_service/call_initiator_svc.py [Modified] - Call initiation
- config/app_config.py [Modified] - Configuration management

**Web Interface:**
- web_interface/app.py [Modified] - FastAPI application
- web_interface/routes_api.py [Modified] - API endpoints
- web_interface/routes_ui.py [Created] - UI routes
- web_interface/static/* [Modified] - Frontend assets
- web_interface/templates/* [Created] - HTML templates

**Common Components:**
- common/data_models.py [Modified] - Shared data models
- common/logger_setup.py [Created] - Logging configuration
- common/redis_client.py [Created] - Redis interface

**Planned Components:**
- audio_processing_service/openai_realtime_client.py
- post_call_analyzer_service/analysis_svc.py
- task_manager/task_lifecycle_manager_svc.py
- task_manager/feedback_manager_svc.py
- campaign_summarizer_service/*

## 4. IMMEDIATE NEXT STEPS (ACTION PLAN)

Focus: Implement UUID handling for stable WebSocket connection

1. Database Updates:
   - Add asterisk_call_uuid column to calls table
   - Update Pydantic models
   - Add UUID-related database operations

2. CallAttemptHandler Updates:
   - Implement UUID generation
   - Update AMI variable passing
   - Add UUID storage in database

3. AudioSocket Components:
   - Update server for UUID parsing
   - Modify handler for UUID management
   - Implement UUID to call_id mapping

4. Testing:
   - Verify UUID generation and storage
   - Test WebSocket connection stability
   - Validate audio stream establishment

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
- MAINTAIN STABILITY (User Instruction): Do not change variable names, database names, or any other fixed components. New additions are fine, but do not alter existing structures in a way that breaks the established flow.
- UPDATE FILE STATUS: In Section 3.2, change the status of files we've worked on from [Planned] to [Created] or [Modified]. Add a concise, one-line summary of each file's purpose if it's new or significantly changed.
- INTEGRATE DECISIONS: Architectural agreements and key decisions from our chat must be woven into Section 2.3. Explain why a decision was made, not just what it was.
- DEFINE NEXT STEPS: Section 4 must always contain a clear, actionable, and specific plan for what we will do in the very next session.

## 2. PROJECT OVERVIEW & CURRENT STATE

### 2.1. Version & Status

Project Version: 14.0

Project Goal: To build a robust, multi-tenant, AI-powered outbound calling system featuring a conversational UI for task definition, an orchestrator for scheduling, a real-time voice AI for calls, an analysis AI for outcomes, and a strategic lifecycle manager for all tasks.

Current Development Phase: Phase 2b (Task Execution Engine & Audio Path - TCP AudioSocket Integration).

Current Focus: Resolving Asterisk's "no activity on AudioSocket connection" timeout after the initial TCP connection and UUID exchange with the Python server. The phone is not ringing yet due to the AudioSocket leg failing.

Next Major Architectural Step: Achieve a stable, bi-directional audio stream between the softphone, Asterisk (via TCP AudioSocket), and the Python `AudioSocketHandler`.

### 2.2. Changelog / Revision History

v14.0 (Current Version):
- **Critical Realization (AudioSocket Type):** Confirmed via `core show application AudioSocket` that the Asterisk instance is using a **raw TCP version of `AudioSocket(uuid,host:port)`**, not a WebSocket version. This was a fundamental misunderstanding in previous approaches.
- **Architecture Pivot (Python Server):**
    - Modified `audio_processing_service/audio_socket_server.py` to be a raw TCP server, removing all WebSocket handshake logic.
    - Heavily modified `audio_processing_service/audio_socket_handler.py`:
        - `__init__` now only takes `reader, writer, redis_client, peername`. `call_id` and `asterisk_call_uuid` are determined after the first frame.
        - `handle_frames` now first reads and expects a `TYPE_UUID` frame from Asterisk, decodes the UUID, and uses it to look up the internal `call_id` via `db_manager.get_call_by_asterisk_uuid`.
        - Implemented a retry loop for the DB lookup of `asterisk_call_uuid` to handle potential timing issues with `CallAttemptHandler`'s DB update.
- **Dialplan Correction:** Updated `extensions.conf` (`[opendeep-ai-leg]`) to use the correct `AudioSocket(${ASTERISK_CALL_UUID},<IP>:<PORT>)` syntax for the TCP version.
- **Bug Fix (Python):** Resolved `NameError: name 'full_audiosocket_uri_with_call_id' is not defined` in `call_processor_service/call_attempt_handler.py` by removing unused variable definitions.
- **Bug Fix (Python):** Resolved `AttributeError: 'AppConfig' object has no attribute 'AUDIOSOCKET_READ_TIMEOUT_S'` by adding `AUDIOSOCKET_READ_TIMEOUT_S` to `config/app_config.py`.
- **Progress:**
    - Successfully established a raw TCP connection from Asterisk's `AudioSocket` application to the Python `AudioSocketServer`.
    - Python `AudioSocketHandler` successfully receives and parses the initial `TYPE_UUID` frame containing the `asterisk_call_uuid` sent by Asterisk.
    - Python `AudioSocketHandler` successfully uses this UUID to look up the internal `call_id` from the database (with retry logic proving effective).
    - Python `AudioSocketHandler` updates the call status to `LIVE_AI_HANDLING` and attempts to send an empty audio frame back to Asterisk.
- **New Issue Identified:** Despite Python sending an empty audio frame, Asterisk still times out with "Reached timeout after 2000 ms of no activity on AudioSocket connection." This causes the `AudioSocket` leg to fail, and consequently, the `Dial()` on the other leg of the `Local` channel does not proceed long enough for the phone to be answered (results in a missed call). The Python handler sees the connection drop as "0 bytes read on a total of 3 expected bytes" when trying to read the next frame.

v13.0:
- **Root Cause Diagnosis (UUID):** Identified that `app_audiosocket.c` (WebSocket version assumption at the time) was erroring with "Failed to parse UUID" because the URI's path component (our integer `call_id`) was not a standard UUID.
- **Architecture Decision (UUID for Asterisk - initial plan):** Decided to generate a `uuid.uuid4()` in `CallAttemptHandler`, store it in `calls.call_uuid`, and use this in the WebSocket URI path.
- **Code Modifications (based on WebSocket assumption):** Updated `schema.sql`, `models.py`, `db_manager.py`, `call_attempt_handler.py`, `audio_socket_server.py`, `audio_socket_handler.py` to handle string UUIDs for the WebSocket path and map them to internal integer `call_id`.

v12.0:
- **Major Success (Call Origination & Dialplan - initial):** Achieved call origination where the phone rang and could be answered, using a `Local` channel and `Wait()` application.
- **AMI Event Reception Verified:** Confirmed Python received AMI events.
- **Issue Narrowed (at the time):** `app_audiosocket.c: Failed to parse UUID` became the main blocker (still assuming WebSocket `AudioSocket(URI,cn)`).

(Older versions summarized for brevity in previous Wayforward versions)

### 2.3. Core Architecture & Key Decisions

- **AudioSocket Protocol (Decision from v14.0):** The system will now use Asterisk's **raw TCP AudioSocket protocol** as defined by `AudioSocket(uuid, host:port)`. The Python server (`AudioSocketServer` and `AudioSocketHandler`) acts as a raw TCP server, expecting the specific binary framing protocol (initial `TYPE_UUID` frame, then audio/DTMF/hangup frames). This supersedes all previous WebSocket-based AudioSocket assumptions.

- **UUID Handling for TCP AudioSocket (Decision from v14.0):**
    1. `CallAttemptHandler` generates a string `asterisk_call_specific_uuid` (e.g., `uuid.uuid4()`).
    2. This UUID is stored in the `calls.call_uuid` column in the database.
    3. This UUID is passed to the Asterisk dialplan via `OPENDDEEP_VARS`.
    4. The dialplan uses this UUID as the *first argument* to `AudioSocket(${ASTERISK_CALL_UUID}, <IP>:<PORT>)`.
    5. Asterisk, upon TCP connection, sends a binary frame of `TYPE_UUID` containing this `ASTERISK_CALL_UUID` as its payload to the Python TCP server.
    6. The Python `AudioSocketHandler` reads this first frame, extracts the `asterisk_call_uuid`, and uses it to look up the internal integer `call_id` from the database.

- **Python Server for Audio (Decision from v14.0):** `audio_processing_service/audio_socket_server.py` is a simple `asyncio` raw TCP server. `audio_processing_service/audio_socket_handler.py` manages the state and frame processing for a single TCP connection from Asterisk, adhering to the binary AudioSocket protocol.

- **AMI Variable Passing (Decision from v11.0, still relevant):** Use a single pipe-separated string for `OPENDDEEP_VARS` passed via AMI `Originate`, parsed in the dialplan using `CUT()`. Current format: `ASTERISK_CALL_UUID|ACTUAL_TARGET_TO_DIAL`.

- **Local Channel for Call Structure (Decision from v11.0, still relevant):**
    - Python originates to `Local/s@opendeep-ai-leg`.
    - The `Originate` action's `Context` parameter directs the second leg to `[opendeep-human-leg]`.
    - `[opendeep-ai-leg]` handles `Answer()` and `AudioSocket()`.
    - `[opendeep-human-leg]` handles `Dial()` to the target.

(Other core decisions regarding AMI client, multi-tenancy, service separation remain as in previous versions unless directly superseded by the AudioSocket type change).

### 3.2. Detailed File Structure & Status

**config/app_config.py** [Modified] - Added `AUDIOSOCKET_READ_TIMEOUT_S`.
**database/db_manager.py** [Modified] - `get_call_by_asterisk_uuid` added. Ensured `**dict(row)` for Pydantic model instantiation.
**call_processor_service/call_attempt_handler.py** [Modified] - `Originate` params updated for `Local/s@opendeep-ai-leg` and `Context: opendeep-human-leg`. Unused variables in `_originate_call` removed. `_process_ami_event` updated.
**extensions.conf (Asterisk)** [Modified] - Contexts `[opendeep-ai-leg]` and `[opendeep-human-leg]` updated to use TCP `AudioSocket(${UUID},${HOST}:${PORT})` syntax.
**audio_processing_service/audio_socket_server.py** [Heavily Modified] - Changed from WebSocket server to raw TCP server. `_handle_new_connection` simplified, passes raw `reader`/`writer` to handler.
**audio_processing_service/audio_socket_handler.py** [Heavily Modified] - Rewritten for raw TCP AudioSocket protocol. `__init__` simplified. `handle_frames` now reads initial `TYPE_UUID` frame, looks up `call_id` by this UUID, then enters main frame loop. Sends an empty audio frame back after initial UUID processing.

---
*Files modified in previous sessions related to UUID for WebSocket path (now less relevant but changes made):*
database/schema.sql [No Change in v14, used existing `call_uuid` column]
database/models.py [No Change in v14, `call_uuid: Optional[str]` was suitable]

---
*Other active files:*
main.py [Modified]
web_interface/app.py [Modified]
task_manager/orchestrator_svc.py [Modified]
task_manager/task_scheduler_svc.py [Modified]
call_processor_service/call_initiator_svc.py [Modified]
call_processor_service/asterisk_ami_client.py [Modified]
config/prompt_config.py [No Change Expected]
llm_integrations/openai_form_client.py [Modified]
llm_integrations/google_gemini_client.py [Created]
task_manager/ui_assistant_svc.py [Modified]
tools/information_retriever_svc.py [Modified]
web_interface/routes_api.py [Modified]
web_interface/routes_ui.py [Created]
web_interface/static/css/style.css [Modified]
web_interface/static/js/main.js [Modified]
web_interface/templates/index.html [Created]
common/data_models.py [Modified]
common/logger_setup.py [Created]
common/redis_client.py [Created]

---
*Planned / Untouched AI processing files:*
[Planned] audio_processing_service/openai_realtime_client.py
[Planned] post_call_analyzer_service/analysis_svc.py
[Planned] task_manager/task_lifecycle_manager_svc.py
[Planned] task_manager/feedback_manager_svc.py
[Planned] campaign_summarizer_service/*

## 4. IMMEDIATE NEXT STEPS (ACTION PLAN)

The immediate focus is to resolve Asterisk's "no activity on AudioSocket connection" timeout, which prevents the call from fully establishing and the phone from ringing properly. The empty audio frame sent by Python was not sufficient.

**Hypothesis:** Asterisk's TCP `AudioSocket` application, after sending its initial `TYPE_UUID` frame, either:
    a) Expects a continuous stream of audio frames (even silence) from the server to keep the socket "active."
    b) Or, it needs to have audio *from the Asterisk channel itself* (e.g., from the `Dial()` leg) to send to the Python server quickly, and if there's none, it times out.

**Next Test: Simplify Dialplan to Force Audio from Asterisk to Python via AudioSocket**

To isolate whether the issue is Python's responsibility to send keep-alives or Asterisk's responsibility to send initial channel audio:

1.  **Temporary Dialplan Change (`extensions.conf`):**
    *   Modify `CallAttemptHandler`'s `_originate_call` to use a new temporary context that *immediately plays audio on the channel before connecting AudioSocket*.
    *   **New Temporary Context `[test-audiosocket-playback-first]`:**
        ```ini
        [test-audiosocket-playback-first]
        exten => s,1,NoOp(==== Test AudioSocket with Early Playback ====)
            same => n,Set(ASTERISK_CALL_UUID=${CUT(OPENDDEEP_VARS,|,1)})
            same => n,Answer()
            ; Play a known, short sound file directly on this channel leg
            ; This ensures there is audio data available on the channel
            ; when AudioSocket starts.
            same => n,Playback(tt-monkeys) ; Or any short sound file like demo-congrats
            same => n,NoOp(Playback finished. Attempting AudioSocket with UUID: ${ASTERISK_CALL_UUID})
            same => n,AudioSocket(${ASTERISK_CALL_UUID},192.168.1.183:1200)
            same => n,NoOp(AudioSocket Status: ${AUDIOSOCKETSTATUS})
            same => n,Wait(5) ; Keep channel alive for a bit to see if audio frames flow
            same => n,Hangup()
        ```
    *   **Temporary Change in `call_processor_service/call_attempt_handler.py` (`_originate_call` method):**
        ```python
        # Inside _originate_call, TEMPORARILY change these for the test:
        originate_action = AmiAction(
            "Originate",
            Channel=f"Local/s@test-audiosocket-playback-first", # Target the new test context
            Context="default", # Second leg of Local channel is not the focus for this specific test
            Exten="s",
            Priority=1,
            # ... (CallerID, Timeout, Async as before) ...
            Variable=f"OPENDDEEP_VARS={self.asterisk_call_specific_uuid}|IGNORED_FOR_THIS_TEST" # Only UUID is used by test context
        )
        ```

2.  **Python `AudioSocketHandler` (`handle_frames`):**
    *   Temporarily **comment out** the section that sends the "initial empty AUDIO frame" *from Python to Asterisk*. We want to see if Asterisk will send audio *first* now that we're forcing playback on its channel.
        ```python
            # ...
            # --- NEW: Send an initial empty audio frame to Asterisk to acknowledge / keep-alive ---
            # COMMENT OUT THIS BLOCK FOR THE TEST
            # try:
            #     logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Sending initial empty AUDIO frame to Asterisk.")
            #     empty_audio_frame_header = struct.pack("!BH", TYPE_AUDIO, 0)
            #     if self.writer and not self.writer.is_closing():
            #         self.writer.write(empty_audio_frame_header)
            #         await self.writer.drain()
            #         logger.info(f"[AudioSocketHandler-TCP:AppCallID={self.call_id},AstDialplanUUID={self.asterisk_call_uuid}] Successfully sent initial empty AUDIO frame.")
            #     else: # ...
            # except Exception as e_send_empty: # ...
            # --- END COMMENT OUT ---

            # --- Main frame processing loop ---
            # ...
        ```

3.  **Execution & Observation:**
    *   `dialplan reload` in Asterisk.
    *   Restart Python application.
    *   Trigger a call from the UI.
    *   **Expected Outcome / Things to Check:**
        *   **Asterisk Logs:**
            *   Does the `Playback(tt-monkeys)` execute?
            *   Does the `AudioSocket(...)` line execute?
            *   Does the "Reached timeout after 2000 ms of no activity" error *still* appear?
        *   **Python Logs:**
            *   Does `AudioSocketHandler` still successfully receive the initial `TYPE_UUID` and map the `call_id`?
            *   **Most importantly:** After the UUID processing, does it start receiving `TYPE_AUDIO` frames from Asterisk (due to the `Playback` application)? Look for `"[AudioSocketHandler-TCP:...] Received AUDIO frame, len=..."` logs.
        *   **Phone Call:** The phone will likely *not* ring in this test because we are not using the `opendeep-human-leg` to `Dial()`. The focus is purely on stabilizing the `AudioSocket` connection on the AI leg.

**Interpreting Results of This Test:**

*   **If Python *receives audio frames* and Asterisk *does not time out*:** This strongly suggests the "no activity" timeout was because the `Local` channel's AI leg (`opendeep-ai-leg`) had no audio to send to the `AudioSocket` application initially. The solution would then involve ensuring that audio from the `Dial()` on the `opendeep-human-leg` is properly bridged or made available to the `opendeep-ai-leg` *before or as* `AudioSocket` connects. This could involve using `Bridge()` or ensuring the `Local` channel itself facilitates this.
*   **If Asterisk *still times out* even with `Playback()` trying to send audio:** This would mean the issue is more likely that `app_audiosocket.c` expects some specific acknowledgment or continuous "keep-alive" frames *from the Python server* beyond the initial empty audio frame we tried. We might need to dig into the `app_audiosocket.c` source or experiment with sending periodic silent audio frames from Python.

This test will help us understand which side (Asterisk not sending, or Python not sending the *right kind* of keep-alive) is causing the timeout.