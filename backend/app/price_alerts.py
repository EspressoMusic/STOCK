"""Price alerts (the user's ask, 13/11/26): a price set on the chart panel (frontend tv.html)
and, once the symbol reaches it, a notification on the phone (push.py) — then it's done.

Checked every minute (scheduler.py) on the 5m candles: the last price, and the highs and
lows of the candles that started after the alert was set, so a spike between two checks
counts too. Above or below is fixed when it's set, by the price then."""
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from . import push
from .candles import Candle, get_candles
from .db import SessionLocal
from .models import PriceAlert

logger = logging.getLogger(__name__)

TIMEFRAME = "5m"


class NoPrice(Exception):
    pass


def shown(symbol: str) -> str:
    """As the chart panel shows it: EURUSD for EURUSD=X, BTC-USD as is."""
    return symbol[:-2] if symbol.endswith("=X") else symbol


def _fmt(price: float) -> str:
    return f"{price:,.5f}".rstrip("0").rstrip(".") if price < 10 else f"{price:,.2f}"


def create(db: Session, symbol: str, price: float) -> PriceAlert:
    # The same alert again (a second click on Create, 14/11/26) is the one already waiting.
    same = (
        db.query(PriceAlert)
        .filter(PriceAlert.symbol == symbol, PriceAlert.price == price, PriceAlert.triggered_at.is_(None))
        .first()
    )
    if same:
        return same
    candles = get_candles(symbol, TIMEFRAME)
    if not candles:
        raise NoPrice(symbol)
    row = PriceAlert(symbol=symbol, price=price, above=int(price > candles[-1].close))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def waiting(db: Session) -> list[PriceAlert]:
    return db.query(PriceAlert).filter(PriceAlert.triggered_at.is_(None)).order_by(PriceAlert.id).all()


def delete(db: Session, alert_id: int) -> bool:
    found = db.query(PriceAlert).filter(PriceAlert.id == alert_id).delete()
    db.commit()
    return bool(found)


def reached(alert: PriceAlert, candles: list[Candle]) -> bool:
    if not candles:
        return False
    set_at = alert.created_at.replace(tzinfo=timezone.utc).timestamp()
    after = [c for c in candles if c.time >= set_at]
    if alert.above:
        return candles[-1].close >= alert.price or any(c.high >= alert.price for c in after)
    return candles[-1].close <= alert.price or any(c.low <= alert.price for c in after)


def check(db: Optional[Session] = None, now: Optional[float] = None):
    """Every alert still waiting, one candle fetch per symbol; never raises."""
    own = db is None
    db = db or SessionLocal()
    try:
        by_symbol: dict[str, list[PriceAlert]] = defaultdict(list)
        for alert in waiting(db):
            by_symbol[alert.symbol].append(alert)
        for symbol, alerts in by_symbol.items():
            try:
                candles = get_candles(symbol, TIMEFRAME)
            except Exception:
                logger.warning("Price alerts: no candles for %s", symbol, exc_info=True)
                continue
            for alert in alerts:
                if not reached(alert, candles):
                    continue
                alert.triggered_at = datetime.fromtimestamp(now or time.time(), timezone.utc).replace(tzinfo=None)
                db.commit()
                arrow = "⬆️" if alert.above else "⬇️"
                push.send(
                    f"🔔 {shown(symbol)} {arrow} {_fmt(alert.price)}",
                    f"המחיר הגיע ל-{_fmt(alert.price)} (עכשיו {_fmt(candles[-1].close)})",
                    url="/",
                    tag=f"price-{alert.id}",
                )
                logger.info("Price alert %s: %s reached %s", alert.id, symbol, alert.price)
    except Exception:
        logger.exception("Price alerts: check failed")
        db.rollback()
    finally:
        if own:
            db.close()
