"""Shared OpenAI client accessor — a single lazily-created client reused by
ai_summary.py, chart_analysis.py, and chat.py. Returns None (rather than
raising) when no API key is configured, so each caller can fall back."""
from typing import Optional

from openai import OpenAI

from .config import settings

_client: Optional[OpenAI] = None


def get_client() -> Optional[OpenAI]:
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client
