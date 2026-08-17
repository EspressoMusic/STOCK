import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import SessionLocal
from .pipeline import run_full_scan_cycle, run_broken_scan_cycle

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=settings.scan_timezone)


def _movers_job():
    logger.info("Running scheduled movers scan cycle")
    db = SessionLocal()
    try:
        run_full_scan_cycle(db)
    finally:
        db.close()


def _broken_job():
    logger.info("Running scheduled broken-stocks scan")
    db = SessionLocal()
    try:
        run_broken_scan_cycle(db)
    finally:
        db.close()


def start_scheduler():
    for label, time_str in (("morning", settings.scan_time_1), ("afternoon", settings.scan_time_2)):
        hour, minute = (int(x) for x in time_str.split(":"))
        scheduler.add_job(
            _movers_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=settings.scan_timezone),
            id=f"scan_{label}",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    hour, minute = (int(x) for x in settings.broken_scan_time.split(":"))
    scheduler.add_job(
        _broken_job,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=settings.scan_timezone),
        id="scan_broken",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: mover scans at %s and %s, broken-stocks scan at %s (%s)",
        settings.scan_time_1, settings.scan_time_2, settings.broken_scan_time, settings.scan_timezone,
    )


def next_run_times() -> dict[str, str | None]:
    out = {}
    for job in scheduler.get_jobs():
        out[job.id] = job.next_run_time.isoformat() if job.next_run_time else None
    return out
