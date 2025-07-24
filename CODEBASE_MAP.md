# OpenDeep Codebase Status Map

This document provides a clear overview of which files are currently active in the system vs legacy files that are preserved for reference.

## 📋 How to Use This Map
- ✅ **ACTIVE FILES** - Currently used by the system when you run `main.py`
- 📦 **LEGACY FILES** - Preserved but not imported or used in current system
- ⚠️ **MIXED FILES** - Files containing both active and legacy sections

## ✅ ACTIVE FILES (Used by main.py and current system)

### Core Entry Points
- `main.py` - Application entry point, service lifecycle management
- `requirements.txt` - Python dependencies
- `README.md` - Project documentation
- `Wayforward.md` - Project state and development history
- `IMPLEMENTATION_PROGRESS.md` - ✅ **NEW** - Detailed phase-by-phase progress tracker

### Project Management & Documentation
- `IMPLEMENTATION_PROGRESS.md` - Comprehensive task breakdown with effort estimates, dependencies, and completion criteria
- `CODEBASE_MAP.md` - File status and implementation tracking (this document)
- `Wayforward.md` - Master project state and architectural decisions

### Audio Processing Service (Core Real-time Audio)
- `audio_processing_service/openai_realtime_client.py` - OpenAI Realtime API integration
- `audio_processing_service/audio_socket_handler.py` - TCP AudioSocket protocol handler
- `audio_processing_service/audio_socket_server.py` - AudioSocket server implementation

### Call Processing Service (Call Management)
- `call_processor_service/call_attempt_handler.py` - Individual call lifecycle management
- `call_processor_service/call_initiator_svc.py` - Call initiation service
- `call_processor_service/asterisk_ami_client.py` - Asterisk AMI communication
- `call_processor_service/redis_command_listener.py` - Redis command processing

### Task Management Service (Campaign & Task Management)
- `task_manager/orchestrator_svc.py` - Campaign orchestration
- `task_manager/task_scheduler_svc.py` - Task scheduling
- `task_manager/task_creation_svc.py` - Task creation logic
- `task_manager/ui_assistant_svc.py` - UI assistant for task definition

### Web Interface (User Interface)
- `web_interface/app.py` - FastAPI application setup
- `web_interface/routes_api.py` - API endpoints
- `web_interface/routes_ui.py` - UI routes
- `web_interface/static/` - Frontend assets (CSS, JS)
- `web_interface/templates/` - HTML templates

### Database Layer
- `database/db_manager.py` - Database operations
- `database/models.py` - Pydantic data models
- `database/schema.sql` - Database schema

### Configuration & Common Services
- `config/app_config.py` - Application configuration
- `common/logger_setup.py` - Logging configuration
- `common/redis_client.py` - Redis client wrapper
- `common/data_models.py` - Shared data models

### LLM Integrations
- `llm_integrations/openai_form_client.py` - OpenAI API client
- `llm_integrations/google_gemini_client.py` - Google Gemini client

### Tools & Utilities
- `tools/information_retriever_svc.py` - External information retrieval

## 📦 LEGACY FILES (Preserved, not actively used)

### Early Development Phase (v1-v12)
- `asty.py` - Original standalone OpenAI Realtime implementation
- `conversation.py` - Original conversation manager
- `config.py` - Early configuration system
- `llm.py` - Early LLM integration
- `logger.py` - Original logging system
- `outboundas.py` - Simple outbound call script
- `storage.py` - Original storage manager

### Experimental Phase (v13-v15)
- `twilio_handler_realtime_experimental_v2.py` - Twilio + OpenAI + Deepgram experiment
- `deepgram_tts_async.py` - Standalone Deepgram TTS implementation

## ⚠️ MIXED FILES (Active + Legacy sections)

### Configuration Files
- `config/prompt_config.py` - **MOSTLY ACTIVE**
  - ✅ Active: `UI_ASSISTANT_SYSTEM_PROMPT`, `ORCHESTRATOR_SYSTEM_PROMPT`, etc.
  - 📦 Legacy: `REALTIME_CALL_LLM_BASE_INSTRUCTIONS` (unused function calling prompts)

## 🚧 POTENTIALLY INCOMPLETE/UNUSED SERVICES

These services exist but may not be fully integrated:
- `post_call_analyzer_service/` - Post-call analysis (planned feature)

## 📁 Directory Structure Summary

```
OpenDeep/
├── ✅ main.py                              # Entry point
├── ✅ audio_processing_service/            # Real-time audio handling  
├── ✅ call_processor_service/              # Call management
├── ✅ task_manager/                        # Campaign & task management
├── ✅ web_interface/                       # User interface
├── ✅ database/                            # Data persistence
├── ✅ common/                              # Shared utilities
├── ✅ config/                              # Configuration
├── ✅ llm_integrations/                    # LLM clients
├── ✅ tools/                               # External tools
├── 🚧 post_call_analyzer_service/          # Future feature
├── 📦 Legacy Files (root level)            # Historical implementations
└── 📁 data/, logs/, recordings/            # Runtime data
```

## 🔄 Version History Context

- **v1-v12**: Single-file implementations, direct integrations
- **v13-v14**: WebSocket experiments, architectural pivots  
- **v15-v16**: Modular architecture, TCP AudioSocket integration
- **v16.0+**: Current stable system with OpenAI Realtime + Asterisk

---

*Last Updated: Progress Tracking System Implementation (v18.1)*
*For detailed implementation progress, see IMPLEMENTATION_PROGRESS.md*
*For more detailed project history, see Wayforward.md*

## 📈 IMPLEMENTATION PROGRESS TRACKING

**Current Phase:** Phase 1 - Foundational Task Lifecycle & Call Retries
**Progress:** 0% Complete (0/7 tasks completed)

### Progress Tracking System Status:
- ✅ **COMPLETED**: `IMPLEMENTATION_PROGRESS.md` created with comprehensive task breakdown
- ✅ **COMPLETED**: `Wayforward.md` updated to version 18.1 with progress tracking architecture
- ✅ **COMPLETED**: `CODEBASE_MAP.md` updated with tracking system integration
- 🔄 **READY**: Phase 1 implementation can now begin with proper tracking in place

### File Status Legend for Implementation:
- 🔄 **In Progress** - Currently being modified
- ✅ **Completed** - Implementation finished and tested
- ⏳ **Pending** - Scheduled for future implementation
- 🚧 **Planned** - Designed but not yet started