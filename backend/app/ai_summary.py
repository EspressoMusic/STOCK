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

    if enrichment.target_mean_price and price:
        if enrichment.target_mean_price > price * 1.1:
            parts.append("אז אישית זה נראה כרגע כמו הזדמנות קנייה")
        elif enrichment.target_mean_price < price * 0.9:
            parts.append("אז אישית הייתי נזהר וקרוב יותר למכירה או הימנעות")
        else:
            parts.append("אז אישית הייתי מחכה ורואה לאן זה מתפתח לפני שמחליטים")
    else:
        parts.append("אז בלי יותר מידע אישית הייתי מחכה ורואה לאן זה מתפתח לפני שמחליטים")

    return " ".join(parts)


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
אתה חבר שמבין במניות ומסביר לחבר שלו בקצרה, ישיר וכן, מה דעתך על הסיכוי \
של מניה להתאושש. יש שני סוגי מקרים: מניה שהתרסקה או זינקה היום, או מניה מרשימת \
"פני-סטוקים שבורים" שכבר זמן רב שווה כמעט כלום ולא בהכרח זזה היום — במקרה הזה \
תתייחס לתמונה הכללית (כמה זמן היא כבר למטה, סיכון להימחק מהמסחר אם המחיר מתחת \
לדולר) ולא רק ל"היום". תדבר בעברית פשוטה בלבד, בגובה העיניים, בלי ז'רגון פיננסי \
מסובך, כאילו אתה שולח הודעת וואטסאפ לחבר. אסור שבתשובה שלך תופיע ולו מילה אחת \
באנגלית או קיצור באנגלית - תרגם כל מונח לעברית (כולל דירוגי אנליסטים כמו buy/ \
hold/sell -> קנייה/החזקה/מכירה). התבסס אך ורק על המספרים והעובדות שכתובים \
במפורש בנתונים שקיבלת למטה (מחיר, אחוז שינוי, יעד אנליסטים אם יש, כותרות חדשות \
אם יש) - אסור לך להוסיף שום עובדה, אירוע, שמועה או "ידע כללי" על החברה שאינו \
כתוב שם במפורש, גם אם נדמה לך שאתה מכיר את החברה ממקור אחר; אם אתה לא בטוח \
במשהו, אל תכתוב אותו. אל תמציא נתונים ואל תכליל באופן גורף. אם אין מספיק מידע \
(בלי אנליסטים ובלי חדשות), תגיד את זה בכנות ותציין שזה הימור עיוור יותר. בסיום \
דבריך תן כיוון ברור וחד-משמעי אחד מתוך שלוש אפשרויות בלבד, ותבסס את הבחירה \
ישירות על ההיגיון הבא: אם יעד האנליסטים גבוה משמעותית מהמחיר הנוכחי או שהחדשות \
חיוביות בבירור - זה נשמע כמו הזדמנות קנייה; אם יעד האנליסטים נמוך משמעותית \
מהמחיר או שהחדשות שליליות בבירור - עדיף למכור/להימנע; ואם הנתונים חסרים, סותרים \
או לא חד-משמעיים - עדיף לחכות ולראות לאן זה מתפתח (זו ברירת המחדל הבטוחה כשאין \
מספיק אינדיקציה ברורה לכיוון אחר). נסח את זה כתחושת בטן אישית ולא כהמלצה \
פורמלית ("אישית הייתי..." או דומה לזה), אבל אל תתחמק ממתן כיוון. שניים עד שלושה \
משפטים לכל היותר, בלי מבוא ובלי סיכום. אל תוסיף בסוף איזה משפט גנרי כמו "תבדוק \
בעצמך" או "זו לא המלצת השקעה" — המשתמש כבר יודע את זה, תתמקד רק בתוכן. אל תסיים \
את התשובה בנקודה."""


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
            max_tokens=180,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or _fallback_summary(symbol, price, change_percent, direction, enrichment, fifty_two_week_change_percent)
    except Exception:
        logger.warning("OpenAI summary generation failed for %s", symbol, exc_info=True)
        return _fallback_summary(symbol, price, change_percent, direction, enrichment, fifty_two_week_change_percent)


_SECTOR_HE = {
    "Technology": "טכנולוגיה",
    "Healthcare": "בריאות",
    "Financial Services": "שירותים פיננסיים",
    "Consumer Cyclical": "מוצרי צריכה מחזוריים",
    "Consumer Defensive": "מוצרי צריכה בסיסיים",
    "Industrials": "תעשייה",
    "Communication Services": "שירותי תקשורת",
    "Energy": "אנרגיה",
    "Utilities": "שירותים ציבוריים",
    "Real Estate": 'נדל"ן',
    "Basic Materials": "חומרי גלם",
}


def _fallback_company_blurb(
    name: Optional[str],
    sector: Optional[str],
    industry: Optional[str],
) -> str:
    sector_he = _SECTOR_HE.get(sector) if sector else None
    if sector_he:
        return f"החברה פועלת בסקטור {sector_he}"
    return "אין מידע זמין על עיסוק החברה"


_COMPANY_SYSTEM_PROMPT = """\
אתה מסביר בעברית פשוטה וברורה בלבד, בפסקה מורחבת של 5-7 משפטים קצרים, במה \
עוסקת חברה - על סמך מידע שתקבל באנגלית (תקציר עסקי, או לפחות סקטור/תחום \
פעילות). תן הסבר מפורט, עשיר ומועיל ככל שהמידע מאפשר, ותכסה כמה שיותר מהנקודות \
הבאות אם יש עליהן מידע בתקציר: מה החברה עושה בפועל ואיך היא מרוויחה כסף, אילו \
מוצרים או שירותים עיקריים היא מציעה, מי הלקוחות או השוק שלה, באילו מדינות או \
אזורים היא פועלת, ומה מייחד אותה מול מתחרות בתחום (אם זה מוזכר). תרגם כל מונח \
באנגלית לעברית, כולל שמות סקטורים ותחומי פעילות (למשל Healthcare -> בריאות, \
Biotechnology -> ביוטכנולוגיה) - אסור שבתשובה שלך תופיע ולו מילה אחת באנגלית \
או קיצור באנגלית, אפילו לא שם מוצר או ראשי תיבות. אל תכתוב את השם המשפטי של \
החברה באנגלית (כולל סיומות כמו Inc/Corp/Ltd) - תתייחס אליה בתור "החברה". כתוב \
במשפטים קצרים וברורים, כאילו אתה מסביר למישהו שלא מכיר בכלל את התחום - בלי \
ז'רגון פיננסי או מקצועי, בלי דעה אישית, בלי המלצת השקעה, רק עובדות על מה \
שהחברה עושה. התבסס אך ורק על מה שכתוב במפורש בתקציר העסקי שקיבלת - אסור לך \
להוסיף עובדות, נתונים, שמות מוצרים, לקוחות, מספרים או פרטים היסטוריים שאינם \
כתובים שם, גם אם נדמה לך שאתה "מכיר" את החברה הזו ממקור אחר או מזיכרון כללי; \
כל פרט בתשובה שלך חייב להיות ניתן לאיתור ישירות בטקסט שקיבלת. אם משהו לא ידוע \
או לא כתוב במפורש, פשוט דלג עליו במקום לנחש או להשלים אותו. שמור על הפסקה \
זורמת וקריאה, לא רשימה של משפטים מנותקים."""


def generate_company_blurb(
    symbol: str,
    name: Optional[str],
    sector: Optional[str],
    industry: Optional[str],
    business_summary: Optional[str],
) -> str:
    client = get_client()
    if client is None:
        return _fallback_company_blurb(name, sector, industry)

    if business_summary:
        user_prompt = f"חברה: {symbol} ({name or 'לא ידוע'})\nתקציר עסקי:\n{business_summary[:1500]}"
    elif sector or industry:
        user_prompt = (
            f"חברה: {symbol} ({name or 'לא ידוע'})\n"
            f"סקטור: {sector or 'לא ידוע'}\nתחום פעילות: {industry or 'לא ידוע'}\n"
            "אין תקציר עסקי מפורט זמין - הסבר בפסקה של 3-4 משפטים, בעברית בלבד "
            "(תרגם את שמות הסקטור/התחום), במה חברות בסקטור ובתחום הזה בדרך כלל "
            "עוסקות, איך הן בדרך כלל מרוויחות כסף ומי בדרך כלל הלקוחות שלהן - "
            "בהתבסס על הסקטור והתחום שלמעלה, בלי להמציא פרטים ספציפיים על החברה "
            "עצמה שאין לך."
        )
    else:
        return _fallback_company_blurb(name, sector, industry)

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _COMPANY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=420,
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


_DAILY_DIGEST_SYSTEM_PROMPT = """\
אתה חבר שמבין במניות ומסכם למשתמש, בפסקה קצרה אחת של 3-5 משפטים, את מצב תיק \
הדמו שלו יחד עם מה שקרה בשוק היום לפי הסריקות. תדבר בעברית פשוטה וישירה, כאילו \
אתה שולח הודעת וואטסאפ לחבר בבוקר. תתייחס לרווח/הפסד הכולל של התיק, להחזקות \
הבולטות (חיוביות ושליליות במיוחד), ולסריקות שמצאו משהו מעניין (עולים/יורדים \
קיצוניים, מניות שבורות, איתותים טכניים) - רק אם יש בהם נתון שרלוונטי. אל תמציא \
מספרים שלא קיבלת. אם אין החזקות פתוחות בתיק, תגיד את זה בקצרה ותעבור ישר \
לסריקות. אל תיתן ייעוץ השקעות פורמלי, רק תמונת מצב. בלי מבוא ובלי סיכום כללי \
בסוף, ישר לעניין."""


def _build_daily_digest_prompt(cash: float, holdings: list[dict], scans: dict) -> str:
    lines = [f"מזומן פנוי בתיק הדמו: ${cash:,.0f}"]

    if holdings:
        total_pnl = sum(
            ((h.get("price") if h.get("price") is not None else h["avg_price"]) - h["avg_price"]) * h["shares"]
            for h in holdings
        )
        lines.append(f"רווח/הפסד כולל בתיק כרגע: {total_pnl:+,.0f}$")
        lines.append("החזקות פתוחות:")
        for h in holdings:
            side = "שורט" if h["shares"] < 0 else "לונג"
            price = h.get("price")
            parts = [f"- {h['symbol']} ({side}, {abs(h['shares']):g} יח')"]
            if price is not None:
                parts.append(f"מחיר נוכחי ${price:.2f} מול מחיר ממוצע ${h['avg_price']:.2f}")
                pnl = (price - h["avg_price"]) * h["shares"]
                parts.append(f"רווח/הפסד {pnl:+.0f}$")
            if h.get("change_percent") is not None:
                parts.append(f"שינוי היום {h['change_percent']:+.1f}%")
            lines.append(" | ".join(parts))
    else:
        lines.append("אין כרגע החזקות פתוחות בתיק הדמו.")

    lines.append("")
    lines.append("סריקות אחרונות:")
    gainers = scans.get("gainers") or []
    losers = scans.get("losers") or []
    if gainers:
        lines.append(
            "עלו הכי חזק היום: "
            + ", ".join(f"{g['symbol']} ({g['change_percent']:+.0f}%)" for g in gainers)
        )
    if losers:
        lines.append(
            "נפלו הכי חזק היום: "
            + ", ".join(f"{l['symbol']} ({l['change_percent']:+.0f}%)" for l in losers)
        )
    if scans.get("broken_count"):
        lines.append(f"ברשימת המניות השבורות יש כרגע {scans['broken_count']} מניות.")
    for label, key in [
        ("פריצת EMA50", "ema_breakout_count"),
        ("מגע EMA50 (4 שעות)", "ema_touch_4h_count"),
        ("אסטרטגיית התבניות", "pattern_strategy_count"),
    ]:
        count = scans.get(key)
        if count:
            lines.append(f"סריקת {label} מצאה {count} מועמדים.")
    if not gainers and not losers and not scans.get("broken_count"):
        lines.append("אין נתוני סריקה טריים זמינים כרגע.")

    return "\n".join(lines)


def _fallback_daily_digest(cash: float, holdings: list[dict], scans: dict) -> str:
    parts: list[str] = []
    if holdings:
        total_pnl = sum(
            ((h.get("price") if h.get("price") is not None else h["avg_price"]) - h["avg_price"]) * h["shares"]
            for h in holdings
        )
        parts.append(f"התיק שלך מכיל {len(holdings)} החזקות עם רווח/הפסד כולל של {total_pnl:+.0f}$")
    else:
        parts.append("אין לך כרגע החזקות פתוחות בתיק הדמו")
    parts.append(f"ומזומן פנוי של ${cash:,.0f}")
    gainers = scans.get("gainers") or []
    losers = scans.get("losers") or []
    if gainers:
        parts.append("היום עלו הכי חזק " + ", ".join(g["symbol"] for g in gainers[:3]))
    if losers:
        parts.append("ונפלו הכי חזק " + ", ".join(l["symbol"] for l in losers[:3]))
    return ". ".join(parts) + "."


def generate_daily_digest(cash: float, holdings: list[dict], scans: dict) -> str:
    """One short paragraph covering the whole demo portfolio + today's scan
    highlights, so the user doesn't have to read every stock separately.
    Portfolio state lives only in the browser (see usePortfolio.js) — the
    frontend sends it on each call, this never touches the DB for it."""
    client = get_client()
    if client is None:
        return _fallback_daily_digest(cash, holdings, scans)

    user_prompt = _build_daily_digest_prompt(cash, holdings, scans)

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _DAILY_DIGEST_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=280,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or _fallback_daily_digest(cash, holdings, scans)
    except Exception:
        logger.warning("OpenAI daily digest generation failed", exc_info=True)
        return _fallback_daily_digest(cash, holdings, scans)


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
