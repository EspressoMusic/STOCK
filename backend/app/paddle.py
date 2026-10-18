"""Paddle Billing webhook handling: HMAC signature verification plus mapping
subscription events onto the single AppLicense row (see license.py). No Paddle
SDK dependency — the signature scheme is simple enough to verify with stdlib
hmac/hashlib, and we only ever read the 'status' field off subscription
events, so we don't need Paddle's full event/object models."""
import hashlib
import hmac
import logging

from .config import settings
from . import license as license_module

logger = logging.getLogger(__name__)

# Statuses that should keep (or grant) AI access. past_due is included so a
# temporarily failed card doesn't instantly cut the owner off mid-retry.
_ACTIVE_STATUSES = {"active", "trialing", "past_due"}
_INACTIVE_STATUSES = {"canceled", "paused"}


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Paddle signs webhooks as 'Paddle-Signature: ts=<unix_ts>;h1=<hex_hmac>',
    where h1 = HMAC-SHA256(webhook_secret, f"{ts}:{raw_body}")."""
    if not settings.paddle_webhook_secret or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(";") if "=" in p)
        ts, h1 = parts["ts"], parts["h1"]
    except (KeyError, ValueError):
        return False

    signed_payload = f"{ts}:".encode("utf-8") + raw_body
    expected = hmac.new(settings.paddle_webhook_secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


def handle_event(payload: dict) -> None:
    event_type = payload.get("event_type", "")
    data = payload.get("data") or {}

    if not event_type.startswith("subscription."):
        logger.info("Ignoring unhandled Paddle event type: %s", event_type)
        return

    status = data.get("status")
    subscription_id = data.get("id")
    customer_id = data.get("customer_id")

    if status in _ACTIVE_STATUSES:
        license_module.set_plan("pro", paddle_subscription_id=subscription_id, paddle_customer_id=customer_id)
        logger.info("Paddle: plan set to pro (subscription %s, status %s)", subscription_id, status)
    elif status in _INACTIVE_STATUSES:
        license_module.set_plan("free", paddle_subscription_id=subscription_id, paddle_customer_id=customer_id)
        logger.info("Paddle: plan set to free (subscription %s, status %s)", subscription_id, status)
    else:
        logger.info("Paddle: ignoring subscription status %r", status)
