import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from . import telegram
from .config import settings
from .daily_zone_4h import run_daily_zone_alerts
from .db import SessionLocal
from .ema_touch3_5m import run_strategy
from .misc_strategies import run_misc_strategies
from .monthly_zone import run_monthly_zone
from .mt5_closed import report_closed
from .penny_scan import run_penny_live
from .pipeline import run_full_scan_cycle, run_broken_scan_cycle
from .touch3_15m import run_touch3_alerts
from .trade_tracker import check_trades
from .weekly_zone import run_weekly_zone

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


def _daily_zone_job():
    db = SessionLocal()
    try:
        run_daily_zone_alerts(db)
    finally:
        db.close()


def _monthly_zone_job():
    db = SessionLocal()
    try:
        run_monthly_zone(db)
    finally:
        db.close()


def _misc_strategies_job():
    db = SessionLocal()
    try:
        run_misc_strategies(db)
    finally:
        db.close()


def _penny_live_job():
    db = SessionLocal()
    try:
        run_penny_live(db)
    finally:
        db.close()


def _weekly_zone_job():
    db = SessionLocal()
    try:
        run_weekly_zone(db)
    finally:
        db.close()


def _touch3_job():
    db = SessionLocal()
    try:
        run_touch3_alerts(db)
    finally:
        db.close()


def _mt5_strategy_job():
    run_strategy()
    db = SessionLocal()
    try:
        report_closed(db)
    finally:
        db.close()


def _trade_updates_job():
    db = SessionLocal()
    try:
        check_trades(db)
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

    # The strategy MetaTrader 5 trades on its own (ema_touch3_5m.py): 5 seconds after each
    # 5m candle closes — its candles come from the terminal, with no delay. Then the trades
    # that closed go to the Nasdaq bot (mt5_closed.py, the user's ask 3/11/26).
    if settings.mt5_mode != "off":
        scheduler.add_job(
            _mt5_strategy_job,
            trigger=CronTrigger(minute="*/5", second=5, timezone="UTC"),
            id="mt5_strategy",
            replace_existing=True,
            misfire_grace_time=60,
        )

    if telegram.is_configured():
        # The stocks bot's type 2 (tight_touch_scan.py) is off since 30/10/26, the user's call
        # ("for now"); since 2/11/26 the stocks bot runs the weekly zone instead (below).
        # The 1h bot ("פורקס המשך"): since 2/11/26 the third touch of a 15m zone
        # (touch3_15m.py), half a minute after each 15m candle closes. Its 15m continuation
        # alerts before (continuation_15m.py) and the 1h retests (retest_alerts.py, off since
        # 29/10/26) stay in the code.
        if settings.telegram_1h_bot_token:
            scheduler.add_job(
                _touch3_job,
                trigger=CronTrigger(day_of_week="mon-fri", minute="*/15", second=30, timezone="Europe/London"),
                id="touch3_15m_alerts",
                replace_existing=True,
                misfire_grace_time=300,
            )
        # The main bot's forex 4h alerts: daily zone, 4h turn, 1h confirmation (daily_zone_4h.py,
        # since 31/10/26 in place of the 3-touch 4h zones, zone_touch_scan.py). They come on a
        # 1h entry candle, so two minutes after each hour closes.
        scheduler.add_job(
            _daily_zone_job,
            trigger=CronTrigger(day_of_week="mon-fri", minute=2, timezone="Europe/London"),
            id="daily_zone_alerts",
            replace_existing=True,
            misfire_grace_time=1800,
        )
        # The 15m drop-and-return alerts (drop_return_scan.py) are off since 29/10/26; their
        # bot (the penny stocks bot) is the "שונות" bot since 2/11/26: five day-trading
        # strategies on US stocks (misc_strategies.py), 20 seconds after each 5m candle of the
        # session closes — the candles' download takes ~40 seconds.
        if settings.telegram_penny_bot_token:
            scheduler.add_job(
                _misc_strategies_job,
                trigger=CronTrigger(
                    day_of_week="mon-fri", hour="9-15", minute="*/5", second=20, timezone=settings.scan_timezone
                ),
                id="misc_strategies_alerts",
                replace_existing=True,
                misfire_grace_time=120,
            )
            # Since 2/11/26 the same bot also gets the small stocks exploding right now
            # (penny_scan.run_penny_live), every 5 minutes of the session — two minutes off the
            # strategies' runs, so the two don't send at once.
            scheduler.add_job(
                _penny_live_job,
                trigger=CronTrigger(
                    day_of_week="mon-fri", hour="9-15", minute="2-59/5", timezone=settings.scan_timezone
                ),
                id="penny_live_alerts",
                replace_existing=True,
                misfire_grace_time=120,
            )
        # The stocks bot's one strategy since 2/11/26: the weekly zone (weekly_zone.py) — the
        # user's pick, in place of SHILO (shilo.py, 5m, code kept). Half an hour after
        # Friday's close, when the week's candle is final.
        if settings.telegram_stocks_bot_token:
            scheduler.add_job(
                _weekly_zone_job,
                trigger=CronTrigger(day_of_week="fri", hour=16, minute=30, timezone=settings.scan_timezone),
                id="weekly_zone_alerts",
                replace_existing=True,
                misfire_grace_time=6 * 3600,
            )
        # The Nasdaq bot's one strategy since 2/11/26: the weekly zone on monthly candles
        # (monthly_zone.py), the user's ask, in place of the NAS100 scalping (nasdaq_scalp.py,
        # code kept). On the 1st of each month, when the month before is final.
        if settings.telegram_nasdaq_bot_token:
            scheduler.add_job(
                _monthly_zone_job,
                trigger=CronTrigger(day=1, hour=1, minute=0, timezone=settings.scan_timezone),
                id="monthly_zone_alerts",
                replace_existing=True,
                misfire_grace_time=12 * 3600,
            )
        else:
            logger.info("No TELEGRAM_NASDAQ_BOT_TOKEN — the monthly zone alerts are off")
        # Every alert's trade, until it reaches its target or stop (trade_tracker.py).
        scheduler.add_job(
            _trade_updates_job,
            trigger=CronTrigger(minute="*/5", second=45, timezone=settings.scan_timezone),
            id="trade_updates",
            replace_existing=True,
            misfire_grace_time=120,
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
