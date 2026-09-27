"""Chat-bot backend: a friendly-tone assistant (same voice as ai_summary.py)
that can discuss stocks, optionally grounded in a real live quote + enrichment
when the user's message names a ticker, and can optionally look at an
attached image (chart, screenshot, anything) the way analyze_chart_image does
for the dedicated chart-scan panel. Stateless — the frontend keeps and resends
the full transcript each turn (see useChatHistory.js), there is no server-side
conversation store."""
import base64
import logging
import re
from typing import Optional

from .config import settings
from .enrichment import enrich_symbol
from .openai_client import get_client
from .quote import get_quote

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"\$([A-Za-z]{1,5})\b|\b([A-Z]{2,5})\b")

_SYSTEM_PROMPT = """\
אתה חבר שמבין במניות, קריפטו ושווקים, ומדבר עם המשתמש בצ'אט בעברית פשוטה \
וישירה, כאילו אתם שולחים הודעות וואטסאפ. אתה יכול לזכור מה נאמר קודם בשיחה \
(היסטוריית ההודעות מצורפת). כשאתה מקבל נתונים אמיתיים על מניה (מחיר, שינוי, \
סקטור, יעד אנליסטים, חדשות) — תתבסס עליהם ולא תמציא מספרים. כשאין לך נתונים \
אמיתיים על משהו, תגיד את זה בכנות במקום לנחש כאילו זו עובדה. אם מצורפת תמונה \
(גרף, צילום מסך וכו') — תתאר מה אתה רואה ותן תחושת בטן, בדיוק כמו שהיית מנתח \
תמונה של סיכויים בהימור ספורט: מסקרן ומהנה, לא מדעי מדויק. \
זה אפליקציית דמו — כל מה שאתה אומר הוא לצורכי הדגמה בלבד ולא ייעוץ השקעות, \
אבל אל תחזור על המשפט הזה בכל הודעה, המשתמש כבר יודע. תשובות קצרות וממוקדות, \
בלי מבוא ארוך. כשאתה מציע או מזכיר מניה ספציפית שכדאי להסתכל עליה, תכתוב את \
הסימול שלה (הטיקר) באותיות אנגליות גדולות בדיוק כמו שהוא נסחר (למשל AAPL או TSLA) \
כדי שהאפליקציה תוכל להציג אותו ככרטיס לחיץ."""

_MAX_SUGGESTIONS = 4


def _extract_candidate_symbols(text: str, max_symbols: int = 2) -> list[str]:
    seen: list[str] = []
    for m in _TICKER_RE.finditer(text or ""):
        sym = (m.group(1) or m.group(2) or "").upper()
        if sym and sym not in seen:
            seen.append(sym)
        if len(seen) >= max_symbols * 3:  # over-collect candidates, most won't resolve to a real quote
            break
    return seen


def _build_market_context(user_text: str) -> Optional[str]:
    candidates = _extract_candidate_symbols(user_text)
    lines: list[str] = []
    resolved = 0
    for symbol in candidates:
        if resolved >= 2:
            break
        quote = get_quote(symbol)
        if quote is None or quote.price is None:
            continue
        resolved += 1
        parts = [f"{quote.symbol} ({quote.name or 'לא ידוע'}): מחיר ${quote.price:.2f}"]
        if quote.change_percent is not None:
            parts.append(f"שינוי היום {quote.change_percent:+.1f}%")
        enrichment = enrich_symbol(symbol)
        if enrichment.sector:
            parts.append(f"סקטור {enrichment.sector}")
        if enrichment.target_mean_price:
            parts.append(f"יעד אנליסטים ממוצע ${enrichment.target_mean_price:.2f}")
        if enrichment.news:
            parts.append(f"כותרת חדשות אחרונה: {enrichment.news[0].title}")
        lines.append(" | ".join(parts))
    if not lines:
        return None
    return "נתונים אמיתיים ורעננים על סמלים שהוזכרו בהודעה:\n" + "\n".join(lines)


def _extract_suggested_stocks(reply_text: str) -> list[dict]:
    candidates = _extract_candidate_symbols(reply_text, max_symbols=_MAX_SUGGESTIONS)
    suggestions: list[dict] = []
    for symbol in candidates:
        if len(suggestions) >= _MAX_SUGGESTIONS:
            break
        quote = get_quote(symbol)
        if quote is None or quote.price is None:
            continue
        suggestions.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "price": quote.price,
                "change_percent": quote.change_percent,
            }
        )
    return suggestions


def generate_chat_reply(
    messages: list[dict],
    image_bytes: Optional[bytes] = None,
    image_content_type: str = "image/png",
) -> tuple[str, list[dict]]:
    client = get_client()
    if client is None:
        return "אין כרגע מפתח OpenAI מוגדר בשרת, אז אני לא יכול לענות בצ'אט. תגדיר OPENAI_API_KEY ותנסה שוב.", []

    last_user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_text = m.get("content") or ""
            break

    chat_messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    market_context = _build_market_context(last_user_text)
    if market_context:
        chat_messages.append({"role": "system", "content": market_context})

    for m in messages[-20:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        chat_messages.append({"role": role, "content": m.get("content") or ""})

    if image_bytes and chat_messages and chat_messages[-1]["role"] == "user":
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{image_content_type};base64,{b64}"
        text_part = chat_messages[-1]["content"] or "מה אתה רואה בתמונה הזו?"
        chat_messages[-1] = {
            "role": "user",
            "content": [
                {"type": "text", "text": text_part},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=chat_messages,
            temperature=0.7,
            max_tokens=350,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return "לא הצלחתי לנסח תשובה כרגע, נסה שוב.", []
        return text, _extract_suggested_stocks(text)
    except Exception:
        logger.warning("Chat completion failed", exc_info=True)
        return "משהו השתבש בפנייה ל-AI. נסה שוב בעוד רגע.", []
