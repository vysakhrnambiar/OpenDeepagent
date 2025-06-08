from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# The path hack is GONE from here.
# Imports now assume the path is already correct.
from . import routes_ui, routes_api
from common.logger_setup import setup_logger
from database.db_manager import initialize_database

logger = setup_logger("WebApp")

app = FastAPI(
    title="OpenDeep Agent Platform",
    description="An AI-powered outbound calling and task management system.",
    version="1.0.0"
)

@app.on_event("startup")
def startup_event():
    logger.info("WebApp starting up...")
    initialize_database()
    logger.info("Database initialization complete.")

@app.on_event("shutdown")
def shutdown_event():
    logger.info("WebApp shutting down...")

app.include_router(routes_api.router, prefix="/api", tags=["API"])
app.include_router(routes_ui.router, tags=["UI"])

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")