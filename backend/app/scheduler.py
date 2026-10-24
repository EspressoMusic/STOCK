import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import telegram
from .config import settings
from .db import SessionLocal
from .pipeline import run_full_scan_cycle, run_broken_scan_cycle
from .retest_alerts import run_retest_alerts
from .zone_touch_scan import run_zone_touch_scan

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


def _retest_alerts_job():
    logger.info("Running scheduled breakout + retest alert scan")
    db = SessionLocal()
    try:
        run_retest_alerts(db)
    finally:
        db.close()


def _zone_touch_job():
    logger.info("Running scheduled 3-touch 4h zone scan")
    run_zone_touch_scan(send=True)


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

    if telegram.is_configured():
        hour, minute = (int(x) for x in settings.retest_alert_time.split(":"))
        scheduler.add_job(
            _retest_alerts_job,
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=settings.scan_timezone),
            id="retest_alerts",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        hour, minute = (int(x) for x in settings.zone_touch_alert_time.split(":"))
        scheduler.add_job(
            _zone_touch_job,
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=settings.scan_timezone),
            id="zone_touch_alerts",
            replace_existing=True,
            misfire_grace_time=3600,
        )
    else:
        logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) — retest and zone-touch alerts are off")

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
