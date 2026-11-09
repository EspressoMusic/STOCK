import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from . import telegram
from .config import settings
from .daily_zone_1h import run_daily_zone_alerts
from .db import SessionLocal
from .mt5_closed import report_closed
from .mt5_opened import report_opened
from .pipeline import run_full_scan_cycle, run_broken_scan_cycle
from .strong_zone_1m import run_strategy
from .stock_day import run_stock_day
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


def _weekly_zone_job():
    db = SessionLocal()
    try:
        run_weekly_zone(db)
    finally:
        db.close()


def _stock_day_job():
    db = SessionLocal()
    try:
        run_stock_day(db)
    finally:
        db.close()


def _daily_zone_job():
    db = SessionLocal()
    try:
        run_daily_zone_alerts(db)
    finally:
        db.close()


def _mt5_strategy_job():
    run_strategy()
    db = SessionLocal()
    try:
        report_opened(db)
        report_closed(db)
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

    # Since 4/11/26 (the user's call) five bots, one strategy each: MetaTrader 5's, the forex
    # bot, long-term stocks and stocks day trading below, and penny stock shorts to come. Only
    # MetaTrader 5 trades: the others mark their zones, no trade drawn or watched
    # (trade_tracker.check_trades, its target/stop replies, isn't scheduled). Off,
    # code kept: "פורקס המשך" (touch3_15m.py, continuation_15m.py), the EUR/USD 1m signals
    # (ema_touch_1m.py), the monthly 50 EMA (monthly_ema.py), the "שונות" bot (misc_strategies.py,
    # penny_scan.run_penny_live), the monthly zone (monthly_zone.py), SHILO (shilo.py) and the
    # stocks' 4h types (tight_touch_scan.py).

    # The strategy MetaTrader 5 trades on its own (strong_zone_1m.py, in place of
    # ema_touch3_5m.py since 4/11/26, its code kept): 5 seconds after each 1m candle closes —
    # its candles come from the terminal, with no delay. Then the trades that opened and
    # closed go to the Nasdaq bot (mt5_opened.py, mt5_closed.py, the user's asks 3-4/11/26).
    if settings.mt5_mode != "off":
        scheduler.add_job(
            _mt5_strategy_job,
            trigger=CronTrigger(second=5, timezone="UTC"),
            id="mt5_strategy",
            replace_existing=True,
            misfire_grace_time=60,
        )

    if telegram.is_configured():
        # The forex bot, the main one (daily_zone_1h.py): daily zone, a 4h candle, then the break
        # and the return on 1h. They come on a 1h candle, so two minutes after each hour closes.
        scheduler.add_job(
            _daily_zone_job,
            trigger=CronTrigger(day_of_week="mon-fri", minute=2, timezone="Europe/London"),
            id="daily_zone_alerts",
            replace_existing=True,
            misfire_grace_time=1800,
        )
        # The 1h bot since 4/11/26: stocks day trading, as signals (stock_day.py), half a minute
        # after each 15m candle closes; the runs before the open read the day's daily zones.
        if settings.telegram_1h_bot_token:
            scheduler.add_job(
                _stock_day_job,
                trigger=CronTrigger(
                    day_of_week="mon-fri", hour="9-15", minute="*/15", second=30, timezone=settings.scan_timezone
                ),
                id="stock_day_alerts",
                replace_existing=True,
                misfire_grace_time=120,
            )
        # The stocks bot since 8/11/26: the weekly zone (weekly_zone.py), as signals — the user's
        # pick, in place of the monthly 50 EMA (monthly_ema.py, code kept). Sunday at 19:00 in
        # Israel, before the week's trading opens in Tel Aviv and New York (the user's pick, 8/11/26).
        if settings.telegram_stocks_bot_token:
            scheduler.add_job(
                _weekly_zone_job,
                trigger=CronTrigger(day_of_week="sun", hour=19, minute=0, timezone=settings.alert_timezone),
                id="weekly_zone_alerts",
                replace_existing=True,
                misfire_grace_time=6 * 3600,
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
