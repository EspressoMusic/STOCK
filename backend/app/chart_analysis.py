"""Best-effort chart-pattern reading from a user-uploaded image, via OpenAI's
vision-capable chat completions. This is a demo feature, not real technical
analysis — the model is looking at a picture, not real price data, and vision
pattern-matching on charts is inherently unreliable. Every result is framed
that way both in the prompt and in the response shape."""
import base64
import json
import logging
from dataclasses import dataclass
from typing import Optional

from .config import settings
from .openai_client import get_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
אתה עוזר שמסתכל על תמונה של גרף מניה/קריפטו/פורקס ומזהה אם יש בו פטרן טכני \
קלאסי — ראש וכתפיים, ראש וכתפיים הפוך, דאבל טופ, דאבל בוטום, משולש (עולה/יורד/\
סימטרי), דגל, כוס וידית, טריז, ערוץ, או תבנית תמיכה/התנגדות ברורה. זה דמו \
בלבד לצורכי בידור — אתה לא רואה נתוני מחיר אמיתיים, רק פיקסלים בתמונה, אז \
הניתוח הוא בגדר "תחושת בטן" חזותית ולא ניתוח טכני אמיתי. תענה אך ורק ב-JSON \
תקני בפורמט הבא, בלי טקסט נוסף מסביב:
{"pattern": "שם הפטרן בעברית, או \\"לא זוהה פטרן ברור\\" אם אין",
 "confidence": מספר שלם 0-100,
 "bias": "bullish" | "bearish" | "neutral",
 "explanation": "משפט או שניים בעברית פשוטה, בטון חבר שמסביר לחבר, בלי ז'רגון \
ובלי לחזור על זה שזה לא ייעוץ השקעות — זה כבר ברור מהקונטקסט."}
אם התמונה לא נראית כמו גרף מסחר בכלל, החזר pattern="לא זוהה גרף בתמונה" \
ו-confidence=0."""


@dataclass
class ChartAnalysis:
    pattern: str
    confidence: int
    bias: str
    explanation: str


def analyze_chart_image(image_bytes: bytes, content_type: str = "image/png") -> Optional[ChartAnalysis]:
    client = get_client()
    if client is None:
        return None

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{content_type};base64,{b64}"

    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "נתח את הגרף שבתמונה הזו."},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            temperature=0.4,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        return ChartAnalysis(
            pattern=str(data.get("pattern") or "לא זוהה פטרן ברור"),
            confidence=max(0, min(100, int(data.get("confidence") or 0))),
            bias=data.get("bias") if data.get("bias") in ("bullish", "bearish", "neutral") else "neutral",
            explanation=str(data.get("explanation") or ""),
        )
    except Exception:
        logger.warning("Chart image analysis failed", exc_info=True)
        return None
