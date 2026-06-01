import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.database import (
    create_crawl_run,
    finish_crawl_run,
    get_config,
    get_jobs_for_run,
    get_latest_completed_run_id,
    get_recent_runs,
    get_run_log,
    init_db,
    set_config,
)
from app.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    setup_scheduler()
    yield


app = FastAPI(title="RoleFinder", lifespan=lifespan)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    prompt = get_config("search_prompt") or ""
    runs = get_recent_runs(10)
    latest_run_id = get_latest_completed_run_id()
    jobs = get_jobs_for_run(latest_run_id) if latest_run_id else []

    crawl_hour = int(os.environ.get("CRAWL_HOUR", "8"))
    crawl_minute = int(os.environ.get("CRAWL_MINUTE", "0"))
    schedule_label = f"{crawl_hour:02d}:{crawl_minute:02d} daily (server local time)"

    return TEMPLATES.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "prompt": prompt,
            "runs": runs,
            "jobs": jobs,
            "latest_run_id": latest_run_id,
            "schedule_label": schedule_label,
        },
    )


# ── Config API ────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    prompt: str


@app.get("/api/config")
def api_get_config():
    return {"search_prompt": get_config("search_prompt")}


@app.post("/api/config")
def api_set_config(body: ConfigUpdate):
    if not body.prompt.strip():
        raise HTTPException(400, "Prompt cannot be empty")
    set_config("search_prompt", body.prompt.strip())
    return {"ok": True}


# ── Crawl API ─────────────────────────────────────────────────────────────────

_active_run_id: int | None = None


@app.post("/api/crawl/trigger")
async def api_trigger_crawl(background_tasks: BackgroundTasks):
    global _active_run_id

    # Prevent double-trigger
    if _active_run_id is not None:
        log = get_run_log(_active_run_id)
        if log and log.get("status") == "running":
            return {"run_id": _active_run_id, "already_running": True}

    run_id = create_crawl_run()
    _active_run_id = run_id

    async def _run():
        global _active_run_id
        from app.crawler import run_crawl
        from app.emailer import send_job_report

        try:
            jobs = await run_crawl(run_id)
            send_job_report(jobs, datetime.now())
        except Exception as exc:
            finish_crawl_run(run_id, "failed", error=str(exc))
            logger.error("Manual crawl %d failed: %s", run_id, exc)
        finally:
            _active_run_id = None

    background_tasks.add_task(_run)
    return {"run_id": run_id, "already_running": False}


@app.get("/api/crawl/runs")
def api_list_runs():
    return get_recent_runs(20)


@app.get("/api/crawl/runs/{run_id}/jobs")
def api_get_jobs(run_id: int):
    return get_jobs_for_run(run_id)


@app.get("/api/crawl/runs/{run_id}/log")
def api_get_log(run_id: int):
    data = get_run_log(run_id)
    if data is None:
        raise HTTPException(404, "Run not found")
    return data
