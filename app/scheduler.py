import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


async def _daily_job():
    from app.crawler import run_crawl
    from app.emailer import send_job_report
    from app.database import create_crawl_run, finish_crawl_run

    logger.info("Daily crawl starting...")
    run_id = create_crawl_run()

    try:
        jobs = await run_crawl(run_id)
        send_job_report(jobs, datetime.now())
        logger.info("Daily crawl complete: %d job(s) found and emailed", len(jobs))
    except Exception as exc:
        finish_crawl_run(run_id, "failed", error=str(exc))
        logger.error("Daily crawl failed: %s", exc)


def setup_scheduler() -> AsyncIOScheduler:
    hour = int(os.environ.get("CRAWL_HOUR", "8"))
    minute = int(os.environ.get("CRAWL_MINUTE", "0"))

    _scheduler.add_job(
        _daily_job,
        CronTrigger(hour=hour, minute=minute),
        id="daily_crawl",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info("Scheduler running — daily crawl at %02d:%02d", hour, minute)
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler
