import logging
import os
from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


async def _run_and_email():
    from app.crawler import run_crawl
    from app.database import (
        create_crawl_run,
        finish_crawl_run,
        get_all_config,
        get_jobs_for_run_by_decision,
    )
    from app.emailer import send_job_report

    run_id = create_crawl_run()
    logger.info("Scheduled crawl starting (run_id=%d)...", run_id)
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
        logger.info("Scheduled crawl complete: %d accepted job(s) emailed", len(accepted))
    except Exception as exc:
        finish_crawl_run(run_id, "failed", error=str(exc))
        logger.error("Scheduled crawl failed: %s", exc)


def setup_scheduler() -> AsyncIOScheduler:
    hour = int(os.environ.get("CRAWL_HOUR", "5"))
    minute = int(os.environ.get("CRAWL_MINUTE", "0"))

    _scheduler.add_job(
        _run_and_email,
        CronTrigger(hour=hour, minute=minute),
        id="daily_crawl",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Scheduler running — daily crawl at %02d:%02d", hour, minute)

    # One-off test run: set TEST_CRAWL_ONCE=HH:MM to fire once today at that time.
    # Remove or leave blank after the test to prevent re-scheduling on next restart.
    once_at = os.environ.get("TEST_CRAWL_ONCE", "").strip()
    if once_at:
        try:
            fire_dt = datetime.combine(
                date.today(),
                datetime.strptime(once_at, "%H:%M").time(),
            )
            if fire_dt > datetime.now():
                _scheduler.add_job(
                    _run_and_email,
                    DateTrigger(run_date=fire_dt),
                    id="test_crawl_once",
                    replace_existing=True,
                )
                logger.info("One-off test crawl scheduled for %s", fire_dt.strftime("%H:%M today"))
            else:
                logger.warning("TEST_CRAWL_ONCE=%s is already in the past — skipped", once_at)
        except Exception as exc:
            logger.warning("Invalid TEST_CRAWL_ONCE value '%s': %s", once_at, exc)

    _scheduler.start()
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler
