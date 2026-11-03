"""Lets the user talk to the alert bots from Telegram. Any message from the alerts chat
gets a reply within seconds with each alert bot's next scan (Israel time) — no reply
means the server is off. "סריקה" instead runs that bot's own scan right away, off its
schedule, and the bot says when it's done; a symbol ("EURUSD", "AAPL 1h") gets its
chart and reading back (symbol_analysis.py). Long-polls getUpdates (one background thread
per bot), so no webhook or public address is needed and it works from the home PC too."""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import symbol_analysis, telegram
from .config import settings
from .daily_zone_4h import run_daily_zone_alerts
from .db import SessionLocal
from .misc_strategies import run_misc_strategies
from .monthly_zone import run_monthly_zone
from .scheduler import scheduler
from .touch3_15m import run_touch3_alerts
from .weekly_zone import run_weekly_zone

logger = logging.getLogger(__name__)

POLL_SECONDS = 10  # Telegram holds getUpdates open this long — under telegram.TIMEOUT_SECONDS
RETRY_SECONDS = 30
# A message sent while the server was off gets no late reply when it comes back up.
MAX_AGE_SECONDS = 60
SCAN_WORDS = {"סריקה", "סרוק", "scan"}

JOBS = (
    ("touch3_15m_alerts", "פורקס 15m (המשך)"),
    ("daily_zone_alerts", "פורקס 4h"),
    ("stock_tight_touch_alerts", "מניות 4h"),
    ("misc_strategies_alerts", "שונות (מניות 5m)"),
    ("weekly_zone_alerts", "מניות שבועי"),
    ("monthly_zone_alerts", "מניות חודשי"),
)
DAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")  # datetime.weekday() order


@dataclass
class Bot:
    job_id: str  # the scheduler job it alerts for (JOBS)
    token: Optional[str]  # None = the main bot
    scan: Callable[[], int]  # runs the scan and sends its alerts; returns how many were sent
    busy: threading.Lock = field(default_factory=threading.Lock)


def when(run_time: datetime, now: datetime) -> str:
    """'היום 14:05', 'מחר 02:05' or 'יום שני 02:05' — run_time and now in the same zone."""
    days_ahead = (run_time.date() - now.date()).days
    day = "היום" if days_ahead == 0 else "מחר" if days_ahead == 1 else f"יום {DAYS[run_time.weekday()]}"
    return f"{day} {run_time:%H:%M}"


def status_text() -> str:
    tz = ZoneInfo(settings.alert_timezone)
    now = datetime.now(tz)
    lines = ["✅ הבוט דולק", "", "הסריקה הבאה (שעון ישראל):"]
    for job_id, name in JOBS:
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            lines.append(f"• {name}: {when(job.next_run_time.astimezone(tz), now)}")
    lines += ["", "לסריקה עכשיו כתוב לבוט: סריקה", "לניתוח כתוב סמל, למשל: EURUSD או AAPL 1h"]
    return "\n".join(lines)


def is_scan_request(text: str) -> bool:
    return text.strip().lstrip("/").lower() in SCAN_WORDS


def done_text(sent: int) -> str:
    if not sent:
        return "✅ הסריקה הסתיימה — אין התראות חדשות"
    return "✅ הסריקה הסתיימה — נשלחה התראה אחת" if sent == 1 else f"✅ הסריקה הסתיימה — נשלחו {sent} התראות"


def _with_db(run: Callable) -> Callable[[], int]:
    def scan() -> int:
        db = SessionLocal()
        try:
            return run(db)
        finally:
            db.close()

    return scan


def _bots() -> list[Bot]:
    """Each bot scans what it alerts for; the "שונות" and stocks bots only if they have
    their own. The stocks bot's is the weekly zone since 2/11/26, in place of SHILO (its 4h
    types are off since 30/10/26); the Nasdaq bot's the monthly zone since 2/11/26.
    The 1h bot ("פורקס המשך")'s is the 15m third touch since 2/11/26."""
    bots = [Bot("daily_zone_alerts", None, _with_db(run_daily_zone_alerts))]
    if settings.telegram_1h_bot_token:
        bots.append(Bot("touch3_15m_alerts", settings.telegram_1h_bot_token, _with_db(run_touch3_alerts)))
    if settings.telegram_penny_bot_token:
        bots.append(Bot("misc_strategies_alerts", settings.telegram_penny_bot_token, _with_db(run_misc_strategies)))
    if settings.telegram_stocks_bot_token:
        bots.append(Bot("weekly_zone_alerts", settings.telegram_stocks_bot_token, _with_db(run_weekly_zone)))
    if settings.telegram_nasdaq_bot_token:
        bots.append(Bot("monthly_zone_alerts", settings.telegram_nasdaq_bot_token, _with_db(run_monthly_zone)))
    return bots


def _scan_now(bot: Bot, chat_id: str):
    """The bot's scan in its own thread (the stocks' takes ~25 minutes), so the bot keeps
    answering meanwhile; a second "סריקה" while it runs doesn't start another."""
    def reply(text: str):
        telegram.send_message(text, chat_id=chat_id, bot_token=bot.token)

    if not bot.busy.acquire(blocking=False):
        reply("⏳ כבר סורק — אודיע כשאסיים")
        return

    def run():
        try:
            reply(f"🔎 מתחיל לסרוק {dict(JOBS)[bot.job_id]} — אודיע כשאסיים")
            logger.info("Telegram asked for a %s scan now", bot.job_id)
            reply(done_text(bot.scan()))
        except Exception:
            logger.exception("On-demand %s scan failed", bot.job_id)
            reply("❌ הסריקה נכשלה")
        finally:
            bot.busy.release()

    threading.Thread(target=run, name=f"telegram-scan-{bot.job_id}", daemon=True).start()


def _analyze(bot: Bot, chat_id: str, request: symbol_analysis.Request):
    """In its own thread: fetching the candles takes a few seconds."""
    def run():
        try:
            logger.info("Telegram asked for an analysis of %s (%s)", request.symbol, request.timeframe)
            symbol_analysis.send(request, chat_id, bot.token)
        except Exception:
            logger.exception("Analysis of %s failed", request.symbol)
            telegram.send_message("❌ הניתוח נכשל", chat_id=chat_id, bot_token=bot.token)

    threading.Thread(target=run, name=f"telegram-analysis-{request.symbol}", daemon=True).start()


def _poll(bot: Bot):
    offset = 0
    while True:
        try:
            updates = telegram._call(
                "getUpdates", bot_token=bot.token, offset=offset, timeout=POLL_SECONDS, allowed_updates=["message"]
            )
        except Exception as exc:
            logger.warning("Telegram getUpdates (%s) failed (%s) — retrying in %ss", bot.job_id, exc, RETRY_SECONDS)
            time.sleep(RETRY_SECONDS)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if chat_id != settings.telegram_chat_id or time.time() - message.get("date", 0) > MAX_AGE_SECONDS:
                continue
            text = message.get("text", "")
            request = symbol_analysis.parse_request(text)
            if is_scan_request(text):
                _scan_now(bot, chat_id)
            elif request:
                _analyze(bot, chat_id, request)
            else:
                telegram.send_message(status_text(), chat_id=chat_id, bot_token=bot.token)


def start():
    if not telegram.is_configured():
        return
    for bot in _bots():
        threading.Thread(target=_poll, args=(bot,), name=f"telegram-{bot.job_id}", daemon=True).start()
    logger.info("Telegram bots listening: any message = status, \"סריקה\" = scan now, a symbol = its analysis")
