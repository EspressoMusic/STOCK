from app import push


def test_alert_title_and_body_from_the_caption():
    text = (
        "🟢 לונג · <b>AAPL &amp; Co</b>\n"
        '<a href="https://www.tradingview.com/chart/?symbol=AAPL">TradingView</a>\n'
        "<b>אזור שבועי</b>"
    )
    push.send_alert(text, "weekly:AAPL")
    push.send.assert_called_once_with("🟢 לונג · AAPL & Co", "אזור שבועי", url="/", tag="weekly:AAPL")


def test_examples_from_history_do_not_go_out():
    push.send_alert("🔴 שורט · <b>EURUSD</b>\nlink\n🕘 דוגמה מההיסטוריה · 01/11 10:00", "dz1h:x")
    push.send.assert_not_called()


def test_make_keys_once(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1", encoding="utf-8")
    first = push.make_keys(env)
    assert push.make_keys(env) == first
    text = env.read_text(encoding="utf-8")
    assert text.startswith("A=1\n") and text.count("VAPID_PRIVATE_KEY=") == 1
