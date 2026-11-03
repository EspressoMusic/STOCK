from datetime import datetime
from zoneinfo import ZoneInfo

from app.status_bot import when

TZ = ZoneInfo("Asia/Jerusalem")
NOW = datetime(2026, 10, 27, 10, 40, tzinfo=TZ)  # a Tuesday


def test_today_and_tomorrow_by_name():
    assert when(datetime(2026, 10, 27, 14, 5, tzinfo=TZ), NOW) == "היום 14:05"
    assert when(datetime(2026, 10, 28, 2, 5, tzinfo=TZ), NOW) == "מחר 02:05"


def test_later_days_by_weekday():
    assert when(datetime(2026, 11, 2, 2, 5, tzinfo=TZ), NOW) == "יום שני 02:05"


def test_scan_request_words():
    from app.status_bot import is_scan_request

    assert is_scan_request("סריקה")
    assert is_scan_request(" סרוק ")
    assert is_scan_request("/scan")
    assert not is_scan_request("מה המצב")


def test_done_text_counts():
    from app.status_bot import done_text

    assert "אין התראות" in done_text(0)
    assert "התראה אחת" in done_text(1)
    assert "נשלחו 7 התראות" in done_text(7)


def test_second_scan_request_while_running_is_refused(monkeypatch):
    import threading

    from app import status_bot

    replies, release = [], threading.Event()
    monkeypatch.setattr(status_bot.telegram, "send_message", lambda text, **kw: replies.append(text) or True)
    bot = status_bot.Bot("stock_tight_touch_alerts", "token", lambda: release.wait() and 3)
    status_bot._scan_now(bot, "1")
    status_bot._scan_now(bot, "1")
    assert any("כבר סורק" in r for r in replies)
    release.set()
    for _ in range(100):
        if not bot.busy.locked():
            break
        threading.Event().wait(0.01)
    assert replies[-1] == status_bot.done_text(3)
