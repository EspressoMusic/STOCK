"""Generates a short, plain-language 'friend explaining to a friend' sentence
about a stock's odds of recovery/continuation, using OpenAI's chat completions
API. Falls back to a template if no API key is set or the call fails, so the
rest of the app keeps working.

Covers two situations (direction): today's extreme movers ('losers'/'gainers'),
and the standing 'broken' penny-stock watchlist, where the interesting number is
the trailing-year collapse, not today's move."""
import json
import logging
from typing import Optional

from .config import settings
from .enrichment import Enrichment
from .openai_client import get_client

logger = logging.getLogger(__name__)


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
    client = get_client()
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


def _fallback_company_blurb(
    name: Optional[str],
    sector: Optional[str],
    industry: Optional[str],
) -> str:
    if industry and sector:
        return f"{name or 'החברה'} פועלת בתחום {industry}, במגזר {sector}."
    if sector:
        return f"{name or 'החברה'} פועלת במגזר {sector}."
    return "אין מידע זמין על עיסוק החברה."


_COMPANY_SYSTEM_PROMPT = """\
אתה מסביר במשפט אחד קצר וברור, בעברית פשוטה, במה עוסקת חברה - על סמך תקציר \
עסקי שתקבל באנגלית. בלי ז'רגון, בלי דעה, בלי המלצת השקעה, רק עובדה: מה החברה \
עושה בפועל. משפט אחד בלבד."""


def generate_company_blurb(
    symbol: str,
    name: Optional[str],
    sector: Optional[str],
    industry: Optional[str],
    business_summary: Optional[str],
) -> str:
    if not business_summary:
        return _fallback_company_blurb(name, sector, industry)

    client = get_client()
    if client is None:
        return _fallback_company_blurb(name, sector, industry)

    user_prompt = f"חברה: {symbol} ({name or 'לא ידוע'})\nתקציר עסקי:\n{business_summary[:1500]}"

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _COMPANY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=100,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or _fallback_company_blurb(name, sector, industry)
    except Exception:
        logger.warning("OpenAI company blurb generation failed for %s", symbol, exc_info=True)
        return _fallback_company_blurb(name, sector, industry)


def _fallback_news_digest(news: list[dict]) -> str:
    return "\n".join(f"• {n.get('title', '')}" for n in news[:3] if n.get("title"))


_NEWS_SYSTEM_PROMPT = """\
אתה עוזר שמסכם כותרות חדשות על מניה בשביל משקיע קמעונאי שאין לו זמן לקרוא. \
לכל ידיעה משמעותית תחזיר שורה אחת קצרה שאומרת בקצרה מה קרה ולמה זה עלול לגרום \
למניה לעלות או לרדת. מזג ידיעות שחוזרות על אותו נושא לשורה אחת. תהיה תמציתי \
ביותר — כמה מילים בודדות לכל שורה, בלי משפטי מבוא ובלי סיכום כללי בסוף, בלי \
המלצת השקעה. עד 3 שורות. כתוב בעברית פשוטה וברורה."""


def generate_news_digest(symbol: str, news: list[dict]) -> str:
    if not news:
        return ""

    client = get_client()
    if client is None:
        return _fallback_news_digest(news)

    lines = [f"מניה: {symbol}", "כותרות אחרונות:"]
    for n in news[:5]:
        title = n.get("title")
        if not title:
            continue
        lines.append(f"- {title} ({n.get('publisher') or 'לא ידוע'})")

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _NEWS_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
            temperature=0.5,
            max_tokens=150,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or _fallback_news_digest(news)
    except Exception:
        logger.warning("OpenAI news digest failed for %s", symbol, exc_info=True)
        return _fallback_news_digest(news)


_WORLD_NEWS_SYSTEM_PROMPT = """\
אתה עוזר שמתרגם ומתמצת כותרות חדשות שוק פיננסי בשביל משקיע ישראלי קמעונאי. \
תקבל רשימת כותרות (לרוב באנגלית, עם שם המקור). לכל כותרת, לפי אותו סדר, החזר: \
1) "title" - תרגום קצר ותמציתי לעברית פשוטה וברורה, עד כ-10 מילים, בלי שם המקור \
ובלי מירכאות. 2) "impact" - משפט קצר אחד בעברית (עד כ-15 מילים) שאומר איך \
הידיעה הזו עשויה להשפיע על השוק - למשל אם זה חיובי/שלילי/מעורב למניות, לריבית, \
לאינפלציה, לסקטור מסוים וכו', בלי המלצת השקעה ובלי ניסוחים גנריים. אם אין מספיק \
מידע להעריך השפעה, כתוב "השפעה לא ברורה כרגע". החזר אך ורק JSON תקין בפורמט: \
{"items": [{"title": "...", "impact": "..."}, ...]} - באותו סדר ובאותה כמות \
פריטים כמו הקלט, בלי שום טקסט נוסף מסביב."""


def _fallback_world_news(news: list[dict]) -> list[dict]:
    return [{"title": n.get("title", ""), "impact": ""} for n in news]


def generate_world_news_hebrew(news: list[dict]) -> list[dict]:
    """Translates+condenses each headline to Hebrew and adds a one-line market
    impact note, via a single batched OpenAI call. Falls back to the original
    (untranslated) titles with no impact note if no API key is set or the call
    fails/returns a malformed shape, so the panel still renders something."""
    if not news:
        return []

    client = get_client()
    if client is None:
        return _fallback_world_news(news)

    lines = [f"{i + 1}. {n.get('title', '')} ({n.get('publisher') or 'לא ידוע'})" for i, n in enumerate(news)]

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _WORLD_NEWS_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
            temperature=0.4,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(text)
        items = parsed.get("items")
        if not isinstance(items, list) or len(items) != len(news):
            raise ValueError("unexpected shape from OpenAI world-news response")
        return [
            {
                "title": (item.get("title") or n.get("title", "")).strip(),
                "impact": (item.get("impact") or "").strip(),
            }
            for item, n in zip(items, news)
        ]
    except Exception:
        logger.warning("OpenAI world-news translation failed", exc_info=True)
        return _fallback_world_news(news)
