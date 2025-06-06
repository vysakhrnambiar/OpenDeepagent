import sys
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# --- Path Hack for correct module imports ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

# Setup the router
router = APIRouter(
    tags=["UI"],
    prefix="/ui"  # All routes in this file will be under /ui
)

# Setup Jinja2 templates to find our index.html
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

@router.get("/", response_class=HTMLResponse)
async def serve_home_page(request: Request):
    """
    Serves the main task creation single-page application (index.html).
    The {"request": request} context is required by Jinja2Templates.
    """
    return templates.TemplateResponse("index.html", {"request": request})