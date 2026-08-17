"""Generates a short, plain-language 'friend explaining to a friend' sentence
about a stock's odds of recovery/continuation, using OpenAI's chat completions
API. Falls back to a template if no API key is set or the call fails, so the
rest of the app keeps working.

Covers two situations (direction): today's extreme movers ('losers'/'gainers'),
and the standing 'broken' penny-stock watchlist, where the interesting number is
the trailing-year collapse, not today's move."""
import logging
from typing import Optional

from openai import OpenAI

from .config import settings
from .enrichment import Enrichment

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def _fallback_summary(
    symbol: str,
    price: Optional[float],
    change_percent: Optional[float],
    direction: str,
    enrichment: Enrichment,
    fifty_two_week_change_percent: Optional[float] = None,
) -> str:
    if direction == "broken":
        price_str = f"${price:.2f}" if price is not None else "כמעט כלום"
        parts = [f"{symbol} נסחרת סביב {price_str}"]
        if fifty_two_week_change_percent is not None:
            parts.append(f"אחרי צניחה של מעל {abs(fifty_two_week_change_percent):.0f}% בשנה האחרונה")
    else:
        pct = f"{abs(change_percent):.0f}%" if change_percent is not None else "בחדות"
        verb = "צנחה" if direction == "losers" else "זינקה"
        parts = [f"{symbol} {verb} {pct} היום"]

    if enrichment.target_mean_price:
        parts.append(f"ואנליסטים בממוצע רואים אותה סביב ${enrichment.target_mean_price:.2f}")
    elif enrichment.news:
        parts.append("ויש עליה כותרות חדשות טריות שכדאי לבדוק")
    else:
        parts.append("אין הרבה כיסוי אנליסטים או חדשות כרגע, אז זה מסוכן יותר לנחש")
    return " ".join(parts) + "."


def _build_prompt(
    symbol: str,
    name: Optional[str],
    price: Optional[float],
    change_percent: Optional[float],
    direction: str,
    enrichment: Enrichment,
    fifty_two_week_change_percent: Optional[float] = None,
) -> str:
    lines = [
        f"מניה: {symbol} ({name or 'לא ידוע'})",
        f"מחיר נוכחי: ${price:.2f}" if price is not None else "מחיר נוכחי: לא ידוע",
    ]

    if direction == "broken":
        lines.append(
            "הקשר: זו לא מניה שזזה במיוחד היום — זו מניה מרשימת מעקב של מניות פני-סטוק "
            "שקרסו, שנשארו זולות מאוד לאורך זמן."
        )
        if fifty_two_week_change_percent is not None:
            lines.append(f"שינוי ב-12 החודשים האחרונים: {fifty_two_week_change_percent:+.0f}% (ירידה חדה)")
        if price is not None and price < 1:
            lines.append("המחיר מתחת ל-$1 — יש סיכון אמיתי שהיא תימחק מהמסחר בנאסד\"ק אם זה נמשך.")
    else:
        lines.append(
            f"שינוי היום: {change_percent:+.1f}%" if change_percent is not None else "שינוי היום: לא ידוע"
        )

    lines.append(f"סקטור: {enrichment.sector or 'לא ידוע'} / {enrichment.industry or ''}")

    if enrichment.target_mean_price:
        lines.append(
            f"יעד מחיר ממוצע של אנליסטים: ${enrichment.target_mean_price:.2f} "
            f"(טווח ${enrichment.target_low_price or 0:.2f}-${enrichment.target_high_price or 0:.2f}, "
            f"{enrichment.num_analyst_opinions or 0} אנליסטים, המלצה: {enrichment.recommendation_key or 'אין'})"
        )
    else:
        lines.append("אין כיסוי אנליסטים זמין למניה הזו.")

    if enrichment.news:
        lines.append("כותרות חדשות אחרונות:")
        for n in enrichment.news[:4]:
            lines.append(f"- {n.title} ({n.publisher or 'לא ידוע'})")
    else:
        lines.append("אין חדשות זמינות למניה הזו לאחרונה.")

    return "\n".join(lines)


_SYSTEM_PROMPT = """\
אתה חבר שמבין במניות ומסביר לחבר שלו במשפט אחד קצר, ישיר וכן מה דעתך על הסיכוי \
של מניה להתאושש. יש שני סוגי מקרים: מניה שהתרסקה או זינקה היום, או מניה מרשימת \
"פני-סטוקים שבורים" שכבר זמן רב שווה כמעט כלום ולא בהכרח זזה היום — במקרה הזה \
תתייחס לתמונה הכללית (כמה זמן היא כבר למטה, סיכון להימחק מהמסחר אם המחיר מתחת \
לדולר) ולא רק ל"היום". תדבר בעברית פשוטה, בגובה העיניים, בלי ז'רגון פיננסי \
מסובך, כאילו אתה שולח הודעת וואטסאפ לחבר. תן תשובה מאוזנת - אל תבטיח כלום ואל \
תיתן ייעוץ השקעות פורמלי, רק תחושת בטן מבוססת על הנתונים שקיבלת (מגמת מחיר, יעד \
אנליסטים אם יש, וכותרות חדשות אם יש). אם אין מספיק מידע (בלי אנליסטים ובלי \
חדשות), תגיד את זה בכנות ותציין שזה הימור עיוור יותר. משפט אחד או שניים לכל \
היותר, בלי מבוא ובלי סיכום. אל תוסיף בסוף איזה משפט גנרי כמו "תבדוק בעצמך" או \
"זו לא המלצת השקעה" — המשתמש כבר יודע את זה, תתמקד רק בתוכן."""


def generate_outlook(
    symbol: str,
    name: Optional[str],
    price: Optional[float],
    change_percent: Optional[float],
    direction: str,
    enrichment: Enrichment,
    fifty_two_week_change_percent: Optional[float] = None,
) -> str:
    client = _get_client()
    if client is None:
        return _fallback_summary(symbol, price, change_percent, direction, enrichment, fifty_two_week_change_percent)

    user_prompt = _build_prompt(
        symbol, name, price, change_percent, direction, enrichment, fifty_two_week_change_percent
    )

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=120,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or _fallback_summary(symbol, price, change_percent, direction, enrichment, fifty_two_week_change_percent)
    except Exception:
        logger.warning("OpenAI summary generation failed for %s", symbol, exc_info=True)
        return _fallback_summary(symbol, price, change_percent, direction, enrichment, fifty_two_week_change_percent)
