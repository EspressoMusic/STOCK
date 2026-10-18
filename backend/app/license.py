"""Whole-deployment plan state ('free' vs 'pro'), backed by a single AppLicense
DB row. This is the one gate every AI call in the app goes through (via
openai_client.get_client) — free always means zero OpenAI requests, regardless
of whether an API key happens to be configured. The row is flipped to 'pro' by
the Paddle webhook (see paddle.py) when a subscription becomes active, and back
to 'free' when it's canceled/paused."""
from datetime import datetime

from .config import settings
from .db import SessionLocal
from .models import AppLicense

_LICENSE_ROW_ID = 1


def _get_or_create(db) -> AppLicense:
    lic = db.get(AppLicense, _LICENSE_ROW_ID)
    if lic is None:
        lic = AppLicense(id=_LICENSE_ROW_ID, plan="free")
        db.add(lic)
        db.commit()
        db.refresh(lic)
    return lic


def get_plan() -> str:
    db = SessionLocal()
    try:
        return _get_or_create(db).plan
    finally:
        db.close()


def is_ai_enabled() -> bool:
    """The single source of truth for whether AI features may run. Requires
    both a configured OpenAI key AND an active Pro plan — either one missing
    means every AI call site falls back to its non-AI template response."""
    return bool(settings.openai_api_key) and get_plan() == "pro"


def set_plan(plan: str, *, paddle_subscription_id: str | None = None, paddle_customer_id: str | None = None) -> AppLicense:
    if plan not in ("free", "pro"):
        raise ValueError(f"invalid plan: {plan!r}")
    db = SessionLocal()
    try:
        lic = _get_or_create(db)
        lic.plan = plan
        if paddle_subscription_id is not None:
            lic.paddle_subscription_id = paddle_subscription_id
        if paddle_customer_id is not None:
            lic.paddle_customer_id = paddle_customer_id
        lic.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(lic)
        return lic
    finally:
        db.close()
