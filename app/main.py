import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from app.database import (
    create_crawl_run,
    finish_crawl_run,
    get_all_config,
    get_all_unique_jobs,
    get_config,
    get_jobs_for_run_by_decision,
    get_latest_completed_run_id,
    get_recent_runs,
    get_run_log,
    init_db,
    set_config,
    set_job_deleted,
    set_job_status,
)
from app.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

JINJA_ENV = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    setup_scheduler()
    yield


app = FastAPI(title="RoleFinder", lifespan=lifespan)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    cfg = get_all_config()
    runs = get_recent_runs(10)
    latest_run_id = get_latest_completed_run_id()
    accepted_jobs = get_all_unique_jobs("accepted")
    rejected_jobs = get_all_unique_jobs("rejected")

    crawl_hour   = int(os.environ.get("CRAWL_HOUR", "5"))
    crawl_minute = int(os.environ.get("CRAWL_MINUTE", "0"))
    schedule_label = f"{crawl_hour:02d}:{crawl_minute:02d} daily (server local time)"

    try:
        qf_lines = "\n".join(json.loads(cfg.get("query_families") or "[]"))
    except Exception:
        qf_lines = cfg.get("query_families") or ""

    html = JINJA_ENV.get_template("dashboard.html").render(
        cfg=cfg,
        qf_lines=qf_lines,
        runs=runs,
        accepted_jobs=accepted_jobs,
        rejected_jobs=rejected_jobs,
        latest_run_id=latest_run_id,
        schedule_label=schedule_label,
    )
    return HTMLResponse(html)


# ── Config API ────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    role_search_prompt:           Optional[str] = None
    evaluator_prompt:             Optional[str] = None
    query_families:               Optional[str] = None   # newline-separated on frontend
    min_accept_score:             Optional[str] = None
    max_source_share_pct:         Optional[str] = None
    include_rejected_near_misses: Optional[str] = None
    # backwards compat
    prompt:                       Optional[str] = None


@app.get("/api/config")
def api_get_config():
    cfg = get_all_config()
    try:
        cfg["query_families_lines"] = "\n".join(json.loads(cfg.get("query_families") or "[]"))
    except Exception:
        cfg["query_families_lines"] = cfg.get("query_families") or ""
    return cfg


@app.post("/api/config")
def api_set_config(body: ConfigUpdate):
    mapping = {
        "role_search_prompt":           body.role_search_prompt,
        "evaluator_prompt":             body.evaluator_prompt,
        "min_accept_score":             body.min_accept_score,
        "max_source_share_pct":         body.max_source_share_pct,
        "include_rejected_near_misses": body.include_rejected_near_misses,
    }
    for key, val in mapping.items():
        if val is not None:
            set_config(key, val.strip() if isinstance(val, str) else val)

    if body.query_families is not None:
        lines = [l.strip() for l in body.query_families.splitlines() if l.strip()]
        set_config("query_families", json.dumps(lines))

    if body.prompt is not None:
        set_config("role_search_prompt", body.prompt.strip())

    return {"ok": True}


# ── Jobs API ──────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
def api_get_all_jobs(decision: str = "accepted"):
    return get_all_unique_jobs(decision)


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: int):
    set_job_deleted(job_id)
    return {"ok": True}


class StatusUpdate(BaseModel):
    status: str  # 'applied', 'maybe', or '' for none


@app.post("/api/jobs/{job_id}/status")
def api_set_status(job_id: int, body: StatusUpdate):
    if body.status not in ("applied", "maybe", ""):
        raise HTTPException(400, "status must be 'applied', 'maybe', or ''")
    set_job_status(job_id, body.status)
    return {"ok": True}


# ── Crawl API ─────────────────────────────────────────────────────────────────

_active_run_id: int | None = None


@app.post("/api/crawl/trigger")
async def api_trigger_crawl(background_tasks: BackgroundTasks):
    global _active_run_id

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
            accepted = await run_crawl(run_id)

            cfg = get_all_config()
            include_nm = cfg.get("include_rejected_near_misses", "true").lower() == "true"
            near_misses = get_jobs_for_run_by_decision(run_id, "rejected")
            near_misses_top5 = sorted(
                near_misses, key=lambda j: -(j.get("fit_score") or 0)
            )[:5]

            send_job_report(
                accepted_jobs=[j for j in accepted if not j.get("duplicate_seen_before")],
                near_misses=near_misses_top5,
                run_date=datetime.now(),
                include_near_misses=include_nm,
            )
        except Exception as exc:
            finish_crawl_run(run_id, "failed", error=str(exc))
            logger.error("Crawl %d failed: %s", run_id, exc)
        finally:
            _active_run_id = None

    background_tasks.add_task(_run)
    return {"run_id": run_id, "already_running": False}


@app.get("/api/crawl/runs")
def api_list_runs():
    return get_recent_runs(20)


@app.get("/api/crawl/runs/{run_id}/jobs")
def api_get_jobs(run_id: int, decision: Optional[str] = None):
    return get_jobs_for_run_by_decision(run_id, decision)


@app.get("/api/crawl/runs/{run_id}/log")
def api_get_log(run_id: int):
    data = get_run_log(run_id)
    if data is None:
        raise HTTPException(404, "Run not found")
    return data
