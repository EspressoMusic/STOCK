"""The user's own trades (their ask, 18/11/26): they send a screenshot of a zone, it's kept
here, and the chart panel (frontend tv.html) shows it as a real chart with the zone drawn, on
any timeframe, zoomable — in place of the picture they got at first, which blurred when zoomed
in (19/11/26). Each new trade goes to one Telegram bot as a link that opens it in the panel.

Kept in a JSON file, not a table: a trade is added from a screenshot by hand (python -m
app.my_trades add ...), the app only reads them. The file is in the frontend's public folder,
so the live site (Vercel, frontend/vercel.json) serves the same list without the backend —
a new trade reaches the phone once it's committed and pushed."""
import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from . import telegram
from .config import settings

TRADES_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "my-trades.json"


def load(path: Path = TRADES_FILE) -> list[dict]:
    """Every trade, newest first."""
    if not path.is_file():
        return []
    trades = json.loads(path.read_text(encoding="utf-8"))
    return sorted(trades, key=lambda t: t["id"], reverse=True)


def add(name: str, symbol: str, bottom: float, top: float, start: str, path: Path = TRADES_FILE) -> dict:
    """A new trade: the zone bottom..top (either order) from start (ISO, UTC if it has no zone)."""
    start_at = datetime.fromisoformat(start)
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    trades = load(path)
    trade = {
        "id": max((t["id"] for t in trades), default=0) + 1,
        "name": name.upper(),
        "symbol": symbol.upper(),
        "bottom": min(bottom, top),
        "top": max(bottom, top),
        "start": start_at.astimezone(timezone.utc).isoformat(),
        "added_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    rows = sorted(trades + [trade], key=lambda t: t["id"])
    path.write_text("[\n" + ",\n".join("  " + json.dumps(t) for t in rows) + "\n]\n", encoding="utf-8")
    return trade


def panel_url(trade: dict) -> str:
    """The chart panel opening this trade."""
    return f"{settings.my_trades_url}?trade={trade['id']}"


def caption(trade: dict) -> str:
    zone = f"{telegram.price(trade['bottom'])}–{telegram.price(trade['top'])}"
    return f"📍 <b>{html.escape(trade['name'])}</b> · אזור {zone}\n{telegram.link(panel_url(trade), 'פתח בפאנל')}"


def send(trade: dict) -> bool:
    """The trade's link to its bot (settings.my_trades_bot)."""
    token, chat = telegram.bot_chat(settings.my_trades_bot)
    return telegram.send_message(caption(trade), chat, token)


def _main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.my_trades")
    sub = parser.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("add", help="add a trade and send its link")
    new.add_argument("name")
    new.add_argument("symbol", help="Yahoo symbol, e.g. EURAUD=X, GC=F, BTC-USD")
    new.add_argument("bottom", type=float)
    new.add_argument("top", type=float)
    new.add_argument("start", help="zone start, ISO UTC, e.g. 2026-11-16T06:00")
    again = sub.add_parser("send", help="send these trades' links again")
    again.add_argument("ids", type=int, nargs="+")
    args = parser.parse_args()

    if args.cmd == "add":
        trades = [add(args.name, args.symbol, args.bottom, args.top, args.start)]
    else:
        trades = [t for t in load() if t["id"] in args.ids]
    for t in trades:
        print(t["id"], t["name"], "sent" if send(t) else "NOT sent", panel_url(t))


if __name__ == "__main__":
    _main()
