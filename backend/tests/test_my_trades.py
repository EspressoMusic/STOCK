from app import my_trades


def test_add_numbers_trades_and_lists_newest_first(tmp_path):
    path = tmp_path / "trades.json"
    assert my_trades.load(path) == []
    first = my_trades.add("xauusd", "gc=f", 3905, 3875, "2026-11-13T09:00", path)
    second = my_trades.add("EURAUD", "EURAUD=X", 1.6287, 1.634, "2026-11-16T08:00+02:00", path)

    assert (first["id"], first["name"], first["symbol"]) == (1, "XAUUSD", "GC=F")
    assert (first["bottom"], first["top"]) == (3875, 3905)  # either order
    assert first["start"] == "2026-11-13T09:00:00+00:00"  # no zone = UTC
    assert second["start"] == "2026-11-16T06:00:00+00:00"
    assert [t["id"] for t in my_trades.load(path)] == [2, 1]


def test_caption_links_to_the_panel(monkeypatch):
    monkeypatch.setattr(my_trades.settings, "my_trades_url", "http://127.0.0.1:5174/tv.html")
    trade = {"id": 5, "name": "EURAUD", "bottom": 1.6287, "top": 1.634}
    text = my_trades.caption(trade)
    assert "EURAUD" in text and "1.6287–1.634" in text
    assert 'href="http://127.0.0.1:5174/tv.html?trade=5"' in text
