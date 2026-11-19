import io

import matplotlib.image as mpimg
import pytest

from app import alert_chart
from app.candles import Candle


def test_ema_of_is_pandas_ewm():
    import pandas as pd

    candles = [Candle(i, 0, 0, 0, 10 + (i * 7) % 5) for i in range(80)]
    expected = pd.Series([c.close for c in candles]).ewm(span=50, adjust=False).mean().tolist()
    assert alert_chart.ema_of(candles, 50) == pytest.approx(expected)


def test_filled_gaps_opens_each_candle_at_the_close_before_it():
    gap_up = [Candle(0, 10, 11, 9, 10.5), Candle(1, 12, 13, 11.5, 12.5)]
    gap_down = [Candle(0, 10, 11, 9, 10.5), Candle(1, 8, 9, 7.5, 8.5)]
    assert alert_chart.filled_gaps(gap_up)[1] == Candle(1, 10.5, 13, 10.5, 12.5)
    assert alert_chart.filled_gaps(gap_down)[1] == Candle(1, 10.5, 10.5, 7.5, 8.5)
    assert alert_chart.filled_gaps(gap_up)[0] == gap_up[0]


def _image(png: bytes):
    return mpimg.imread(io.BytesIO(png))[:, :, :3]


def test_a_framed_picture_is_black_on_every_edge():
    candles = [Candle(i, 10 + i, 11 + i, 9 + i, 10.5 + i) for i in range(20)]
    chart = (candles, "x", None, None, None), dict(gapless=True)
    framed = _image(alert_chart.render_side_by_side(chart, chart, divider=True, frame=True))
    for edge in (framed[:4], framed[-4:], framed[:, :4], framed[:, -4:]):
        assert edge.max() < 0.1
    assert _image(alert_chart.render_side_by_side(chart, chart))[:4].min() > 0.9


def test_plain_leaves_out_the_marks_but_frames_the_view_on_them(monkeypatch):
    candles = [Candle(i * 60, 10 + i % 3, 11 + i % 3, 9 + i % 3, 10.5 + i % 3) for i in range(200)]
    first = alert_chart.datetime.fromtimestamp(50 * 60, alert_chart.timezone.utc).isoformat()
    calls = []
    monkeypatch.setattr(alert_chart, "_number", lambda *a: calls.append(a))
    fig = alert_chart.Figure()
    ax = fig.add_subplot()
    alert_chart.draw(ax, candles, "x", None, None, None, numbers=[(first, 10)], breakout=first, plain=True, min_bars=10)
    assert calls == [] and not ax.collections and ax.get_xticks().size == 0  # no number, arrow, dot or time
    assert ax.get_xlim()[1] - ax.get_xlim()[0] > 150 - alert_chart.LEAD_BARS  # from before the numbered candle
