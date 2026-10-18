"""Shared OpenAI client accessor — a single lazily-created client reused by
ai_summary.py, chart_analysis.py, and chat.py. Returns None (rather than
raising) whenever AI shouldn't run right now — no API key configured, or the
deployment is on the free plan (see license.py) — so each caller falls back
to its template/no-AI response. This is the one choke point every AI call in
the app passes through, so gating here is enough to guarantee the free plan
never generates OpenAI cost."""
from typing import Optional

from openai import OpenAI

from .config import settings
from .license import is_ai_enabled

_client: Optional[OpenAI] = None


def get_client() -> Optional[OpenAI]:
    global _client
    if not is_ai_enabled():
        return None
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client
