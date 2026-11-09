"""The weekly stocks, stock day trading and forex alerts, kept for the app's signals page
(the user's ask, 8/11/26): each one as it went out — side, stock or pair, the caption's
reasons and its chart picture. A strategy is told by its key's prefix, not its bot, so a
bot that changes strategy doesn't mix the two on the page.

Only alerts that are also in SentAlert are listed: an example sent from a module's
__main__ goes through the same send, but isn't a live signal. Examples from history are
listed too when kept as such (example_at), marked on the page (the user's ask, 9/11/26: old
examples in each strategy's history)."""
import html
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import SentAlert, SignalLog

logger = logging.getLogger(__name__)

# The page's tabs: weekly_zone, stock_day, daily_zone_1h.
KINDS = {"weekly": "weekly:", "day": "stock-day:", "forex": "dz1h:"}
PICTURE_DIR = Path(__file__).resolve().parent.parent / "signal_pictures"


def kind_of(key: str) -> Optional[str]:
    return next((kind for kind, prefix in KINDS.items() if key.startswith(prefix)), None)


def _plain(line: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", line)).strip()


def _caption(text: str) -> tuple[Optional[str], bool, Optional[str]]:
    """Side, strong and the reasons, from telegram.alert_caption's two lines and what follows."""
    lines = text.split("\n")
    head = lines[0]
    direction = "up" if head.startswith("🟢") else "down" if head.startswith("🔴") else None
    details = "\n".join(p for p in map(_plain, lines[2:]) if p) or None
    return direction, "💪" in head, details


def picture_path(signal_id: int) -> Path:
    return PICTURE_DIR / f"{signal_id}.png"


def record(
    key: str, symbol: str, name: Optional[str], text: str, png: Optional[bytes], db: Optional[Session] = None,
    example_at: Optional[datetime] = None,
):
    """Keeps an alert that just went out, if it's one of the page's; never raises. example_at
    (UTC): an example from history instead, kept as if it went out then."""
    kind = kind_of(key)
    if kind is None:
        return
    own = db is None
    db = db or SessionLocal()
    try:
        row = db.query(SignalLog).filter(SignalLog.key == key).first()
        if row is None:
            row = SignalLog(key=key, kind=kind, symbol=symbol)
            db.add(row)
        row.name = name
        row.direction, strong, row.details = _caption(text)
        row.strong = int(strong)
        row.example = int(example_at is not None)
        if example_at is not None:
            row.created_at = example_at
        db.flush()  # the id names the picture
        if png is not None:
            PICTURE_DIR.mkdir(exist_ok=True)
            picture_path(row.id).write_bytes(png)
        row.has_picture = int(png is not None)
        db.commit()
    except Exception:
        logger.exception("Signal log: couldn't keep %s", key)
        db.rollback()
    finally:
        if own:
            db.close()


def latest(db: Session, kind: str, limit: int) -> list[SignalLog]:
    """The kind's live signals and examples, newest first."""
    return (
        db.query(SignalLog)
        .outerjoin(SentAlert, SentAlert.key == SignalLog.key)
        .filter(SignalLog.kind == kind, or_(SentAlert.id.isnot(None), SignalLog.example == 1))
        .order_by(SignalLog.created_at.desc(), SignalLog.id.desc())
        .limit(limit)
        .all()
    )
