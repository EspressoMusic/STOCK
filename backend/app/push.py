"""Notifications on the phone from the app itself, not Telegram (the user's ask, 13/11/26):
the strategies' alerts (trade_tracker.send_alert) and price alerts (price_alerts.py), as
Web Push to every phone that turned them on in the app's settings.

The phone reaches the app over https through Tailscale (`tailscale serve --bg 5174`); an
iPhone only gets them from the app added to its home screen. The key pair is in
backend/.env (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY), made once by `python -m app.push`."""
import base64
import html
import json
import logging
import re
import threading
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import PushSubscription

logger = logging.getLogger(__name__)

TTL_SECONDS = 6 * 3600  # a phone that's off longer than this skips the notification


def is_configured() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def subscribe(db: Session, endpoint: str, p256dh: str, auth: str):
    row = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if row is None:
        row = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth)
        db.add(row)
    row.p256dh, row.auth = p256dh, auth
    db.commit()


def unsubscribe(db: Session, endpoint: str):
    db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).delete()
    db.commit()


def _send_all(payload: str):
    db = SessionLocal()
    try:
        for sub in db.query(PushSubscription).all():
            try:
                webpush(
                    subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                    data=payload,
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject},
                    ttl=TTL_SECONDS,
                    timeout=10,
                )
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (404, 410):  # the phone turned them off or the app was removed
                    db.delete(sub)
                    db.commit()
                else:
                    logger.warning("Push: not sent to %s… (%s)", sub.endpoint[:40], exc)
            except Exception:
                logger.exception("Push: not sent to %s…", sub.endpoint[:40])
    finally:
        db.close()


def send(title: str, body: str = "", url: str = "/", tag: Optional[str] = None):
    """To every phone that turned them on, in the background; never raises."""
    if not is_configured():
        return
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    threading.Thread(target=_send_all, args=(payload,), daemon=True).start()


def _plain(line: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", line)).strip()


def send_alert(text: str, key: str):
    """A strategy's alert that just went out on Telegram (its caption): its first line as the
    title ("🟢 לונג · AAPL"), the lines under the TradingView link as the body. Not examples
    from history, which only a manual run sends."""
    if "דוגמה מההיסטוריה" in text:
        return
    lines = [_plain(line) for line in text.split("\n")]
    body = "\n".join(line for line in lines[2:] if line)
    send(lines[0], body, url="/", tag=key)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_keys(env_path: Path) -> str:
    """A new key pair into backend/.env, unless it has one already. The public key."""
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    found = re.search(r"^VAPID_PUBLIC_KEY=(\S+)", text, re.M)
    if found and re.search(r"^VAPID_PRIVATE_KEY=\S+", text, re.M):
        return found.group(1)
    key = ec.generate_private_key(ec.SECP256R1())
    private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public = _b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    lines = "\n# Phone notifications from the app (app/push.py)\n" f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}\n"
    with env_path.open("a", encoding="utf-8") as f:
        f.write(("" if text.endswith("\n") or not text else "\n") + lines)
    return public


if __name__ == "__main__":
    print("VAPID_PUBLIC_KEY =", make_keys(Path(__file__).resolve().parent.parent / ".env"))
