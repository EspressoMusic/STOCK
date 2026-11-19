"""Outgoing Telegram messages through the Bot API — no library, just HTTPS calls.

Setup: create a bot with @BotFather, put its token in backend/.env as
TELEGRAM_BOT_TOKEN, send the bot any message from your own Telegram account, then run
`python -m app.telegram` from backend/ — it prints your chat id (goes in .env as
TELEGRAM_CHAT_ID) and sends a test message."""
import html
import json
import logging
import ssl
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote, urlencode

import requests
import truststore
import yfinance as yf
from requests.adapters import HTTPAdapter

from .config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 15
UPLOAD_TIMEOUT_SECONDS = 60

# Yahoo's exchange codes → TradingView's: Nasdaq's three tiers, the NYSE, NYSE American
# and Arca (TradingView lists both as AMEX), OTC.
TRADINGVIEW_EXCHANGES = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "AMEX", "PCX": "AMEX",
    "PNK": "OTC", "OQB": "OTC", "OQX": "OTC",
}
FOREX_EXCHANGE = "OANDA"
# Each stock's TradingView exchange, once Yahoo told it: {"AAPL": "NASDAQ"}.
EXCHANGE_CACHE = Path(__file__).resolve().parent.parent / "tradingview_exchanges.json"
_exchanges: Optional[dict] = None
_exchanges_lock = threading.Lock()


class _SystemTrustAdapter(HTTPAdapter):
    """Verify TLS against the OS trust store instead of certifi's bundle — an HTTPS
    filter on the line (e.g. Netspark) re-signs api.telegram.org with its own root CA,
    which Windows trusts but certifi doesn't."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().init_poolmanager(*args, **kwargs)


_session = requests.Session()
_session.mount("https://", _SystemTrustAdapter())


def is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _call(method: str, files: Optional[dict] = None, bot_token: Optional[str] = None, **params) -> dict:
    url = f"{API_BASE}/bot{bot_token or settings.telegram_bot_token}/{method}"
    if files:  # an upload goes as multipart form fields, not JSON
        resp = _session.post(url, data=params, files=files, timeout=UPLOAD_TIMEOUT_SECONDS)
    else:
        resp = _session.post(url, json=params, timeout=TIMEOUT_SECONDS)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description') or resp.status_code}")
    return data["result"]


def post_message(
    text: str, chat_id: Optional[str] = None, bot_token: Optional[str] = None, reply_to: Optional[int] = None
) -> Optional[int]:
    """HTML-formatted message to the configured chat, from the main bot unless another
    bot's token is given, as a reply to message reply_to if given (still sent if that
    one is gone); its message id. Returns None (and logs) on failure rather than
    raising, so one bad send doesn't stop an alert run."""
    if not (bot_token or settings.telegram_bot_token):
        logger.warning("Telegram message not sent: TELEGRAM_BOT_TOKEN is not set")
        return None
    params = dict(chat_id=chat_id or settings.telegram_chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    try:
        return _call("sendMessage", bot_token=bot_token, **params)["message_id"]
    except Exception:
        logger.exception("Telegram sendMessage failed")
        return None


def send_message(text: str, chat_id: Optional[str] = None, bot_token: Optional[str] = None) -> bool:
    """post_message, whether it went out."""
    return post_message(text, chat_id, bot_token) is not None


def post_photo(
    png: bytes, caption: str = "", chat_id: Optional[str] = None, bot_token: Optional[str] = None,
    reply_to: Optional[int] = None,
) -> Optional[int]:
    """A PNG with an HTML caption (the alert's text). Otherwise as post_message."""
    if not (bot_token or settings.telegram_bot_token):
        logger.warning("Telegram photo not sent: TELEGRAM_BOT_TOKEN is not set")
        return None
    params = dict(chat_id=chat_id or settings.telegram_chat_id, caption=caption, parse_mode="HTML")
    if reply_to:  # a multipart form field, so the object goes as JSON text
        params["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    try:
        return _call("sendPhoto", files={"photo": ("chart.png", png, "image/png")}, bot_token=bot_token, **params)["message_id"]
    except Exception:
        logger.exception("Telegram sendPhoto failed")
        return None


def send_photo(png: bytes, caption: str = "", chat_id: Optional[str] = None, bot_token: Optional[str] = None) -> bool:
    """post_photo, whether it went out."""
    return post_photo(png, caption, chat_id, bot_token) is not None


def bot_chat(bot: str) -> tuple[Optional[str], str]:
    """(token, chat id) of the bot named: "main", or a strategy's own bot — "1h",
    "stocks", "nasdaq", "penny", "scalp" or "ema" (settings.telegram_<name>_bot_token). A token of None
    is the main bot's; a bot's blank chat id is the main chat."""
    if bot == "main":
        return None, settings.telegram_chat_id
    token = getattr(settings, f"telegram_{bot}_bot_token") or None
    return token, getattr(settings, f"telegram_{bot}_chat_id") or settings.telegram_chat_id


def _yahoo_exchange(symbol: str) -> Optional[str]:
    """Yahoo's exchange code for a stock ("NMS"), or None if Yahoo has none or can't be reached."""
    for _ in range(2):  # Yahoo drops a call now and then
        try:
            quotes = yf.Search(symbol, max_results=5, news_count=0).quotes
        except Exception:
            continue
        return next((q.get("exchange") for q in quotes if q.get("symbol") == symbol), None)
    logger.warning("No exchange for %s: Yahoo's search failed", symbol)
    return None


def tradingview_exchange(symbol: str, lookup: bool = True) -> Optional[str]:
    """TradingView's exchange for a stock ("NASDAQ"), from EXCHANGE_CACHE, else (when
    lookup) from Yahoo, kept there. None when not known."""
    global _exchanges
    with _exchanges_lock:
        if _exchanges is None:
            try:
                _exchanges = json.loads(EXCHANGE_CACHE.read_text())
            except (OSError, ValueError):
                _exchanges = {}
        if symbol in _exchanges or not lookup:
            return _exchanges.get(symbol)
    exchange = TRADINGVIEW_EXCHANGES.get(_yahoo_exchange(symbol))
    if exchange:
        with _exchanges_lock:
            _exchanges[symbol] = exchange
            EXCHANGE_CACHE.write_text(json.dumps(_exchanges, indent=0, sort_keys=True))
    return exchange


def tradingview_url(symbol: str, lookup: bool = True) -> str:
    """TradingView's chart of a Yahoo symbol, the exchange in front ("NASDAQ:AAPL"): with a
    bare "AAPL" TradingView's phone app keeps its last chart open (the user found, 11/11/26).
    lookup=False takes a stock's exchange from EXCHANGE_CACHE only, for a list of many."""
    if symbol.endswith("=X"):  # Yahoo's "GBPCHF=X"
        symbol = f"{FOREX_EXCHANGE}:{symbol.removesuffix('=X')}"
    elif symbol.endswith(".TA"):
        symbol = f"TASE:{symbol.removesuffix('.TA')}"
    elif exchange := tradingview_exchange(symbol, lookup):
        symbol = f"{exchange}:{symbol.replace('-', '.')}"  # Yahoo's "BRK-B" is NYSE's "BRK.B"
    return f"https://www.tradingview.com/chart/?symbol={quote(symbol)}"


def price(value: float) -> str:
    """As the chart shows it: 1.09825 for a forex pair, 152.37 for USD/JPY or a stock."""
    return f"{value:g}"


def _unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def chart_window_url(
    symbol: str,
    timeframe: str,
    zone_bottom: float,
    zone_top: float,
    zone_start: str,
    direction: Optional[str] = None,
    breakout: Optional[str] = None,
    retests: Sequence[str] = (),
    touches: Sequence[str] = (),
) -> str:
    """The scanner (settings.alert_chart_url) opening this symbol's chart on this
    timeframe, drawing the alert's own marking: the zone from zone_start, and the
    breakout + retest candles or the touch candles (ISO times). The frontend's
    lib/alertLink.js readAlert reads these parameters."""
    params = {"symbol": symbol, "tf": timeframe, "zone": f"{zone_bottom},{zone_top}", "from": _unix(zone_start)}
    if direction:
        params["dir"] = direction
    if breakout:
        params["breakout"] = _unix(breakout)
    if retests:
        params["retests"] = ",".join(str(_unix(t)) for t in retests)
    if touches:
        params["touches"] = ",".join(str(_unix(t)) for t in touches)
    return f"{settings.alert_chart_url}?{urlencode(params, safe=',')}"


def link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


def alert_caption(symbol: str, name: Optional[str], long: bool, tag: str = "") -> str:
    """The caption under every alert's chart picture: long or short and the pair/stock,
    then its TradingView link — nothing else (the user's ask, 27/10/26: each strategy
    has its own bot, and the picture shows the zone). tag tells apart two strategies
    sharing a bot ("סוג 2")."""
    head = "🟢 לונג" if long else "🔴 שורט"
    tail = f" · {tag}" if tag else ""
    return f"{head} · <b>{html.escape(name or symbol)}</b>{tail}\n{link(tradingview_url(symbol), 'TradingView')}"


def recent_chats() -> list[tuple[str, str]]:
    """(chat id, who) for everyone who messaged the bot in the last 24 hours."""
    chats: dict[str, str] = {}
    for update in _call("getUpdates"):
        chat = (update.get("message") or update.get("my_chat_member") or {}).get("chat")
        if chat:
            who = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
            chats[str(chat["id"])] = who
    return list(chats.items())


if __name__ == "__main__":
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in backend/.env first.")
    me = _call("getMe")
    print(f"Bot: @{me['username']}")
    if settings.telegram_chat_id:
        ok = send_message("✅ הבוט מחובר — מכאן יגיעו התראות אסטרטגיה 4 (פורקס).")
        print("Test message sent." if ok else "Sending failed — see the log above.")
        for label, token, chat, strategy in (
            ("Strategy 1", settings.telegram_1h_bot_token, settings.telegram_1h_chat_id, "אסטרטגיה 1 (פורקס, שעה)"),
            ("Stocks", settings.telegram_stocks_bot_token, settings.telegram_stocks_chat_id, "אסטרטגיה 4 · מניות"),
            ("Misc", settings.telegram_penny_bot_token, settings.telegram_penny_chat_id, "שונות (מניות 5m)"),
        ):
            if not token:
                continue
            other = _call("getMe", bot_token=token)
            ok = send_message(f"✅ הבוט מחובר — מכאן יגיעו התראות {strategy}.", chat_id=chat or None, bot_token=token)
            print(f"{label} bot @{other['username']}: " + (
                "test message sent." if ok else f"sending failed — press Start in @{other['username']} first."
            ))
    else:
        chats = recent_chats()
        if not chats:
            raise SystemExit(f"No messages yet — send @{me['username']} any message in Telegram, then run this again.")
        for chat_id, who in chats:
            print(f"chat id {chat_id}  ({who})")
        print("Put the right one in backend/.env as TELEGRAM_CHAT_ID.")
