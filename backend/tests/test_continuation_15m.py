from app.candles import Candle
from app.continuation_15m import Base, find_base, find_setups, second_break


def bar(t, o, h, l, c):
    return Candle(time=t, open=o, high=h, low=l, close=c)


def hours(*rows):
    return [bar(i * 3600, *row) for i, row in enumerate(rows)]


# Price steps down from 110 (a typical 1h candle of about 1.5, gaps included), pauses at
# 101-102 for two candles, then a red candle closes at 99.5 — under the pause and every
# low before it.
DRIFT = [(110 - i, 110.5 - i, 109.5 - i, 110 - i) for i in range(8)]
PAUSE = [(101.5, 102, 101, 101.8), (101.8, 102, 101.2, 101.4)]
BREAK = (101.4, 101.5, 99.3, 99.5)


def test_a_clean_break_out_of_a_small_pause():
    base = find_base(hours(*DRIFT, *PAUSE, BREAK), 10)
    assert (base.dir, base.bottom, base.top) == ("down", 101, 102)


def test_a_tall_pause_is_chop():
    tall = [(101.5, 104, 101, 101.8), (101.8, 102, 101.2, 101.4)]
    assert find_base(hours(*DRIFT, *tall, BREAK), 10) is None


def test_a_small_breakout_candle_is_no_break():
    assert find_base(hours(*DRIFT, *PAUSE, (101.4, 101.5, 100.6, 100.8)), 10) is None


def test_a_break_into_recent_lows_is_not_clean():
    dip = [*DRIFT[:7], (103, 103.2, 99.4, 102)]  # price was down at 99.4 an hour before the pause
    assert find_base(hours(*dip, *PAUSE, BREAK), 10) is None


BASE = Base(idx=0, dir="down", bottom=101, top=102, unit=1)


def quarters(*rows):
    return [bar(i * 900, *row) for i, row in enumerate(rows)]


# The user's GBP/CAD on 28/10/26, in round numbers: back inside, a red close under the
# zone, back inside again, and a red entry candle closing under it again.
TWICE = [
    (99.5, 99.8, 99.2, 99.6),
    (99.6, 101.6, 99.5, 101.5),  # 1 back inside
    (101.5, 101.6, 100.6, 100.7),  # first break
    (100.7, 101.4, 100.6, 101.3),  # 3 back inside again
    (101.3, 101.7, 101.1, 101.6),
    (101.6, 101.7, 100.5, 100.6),  # 5 second break, under the candle before's body
]


def test_back_twice_and_broke_away_twice():
    assert second_break(quarters(*TWICE), 0, BASE) == (3, 5)


def test_one_break_is_not_enough():
    assert second_break(quarters(*TWICE[:5]), 0, BASE) is None


def test_a_close_over_the_zone_ends_the_setup():
    rows = [*TWICE[:4], (101.3, 102.5, 101.2, 102.3), TWICE[5]]
    assert second_break(quarters(*rows), 0, BASE) is None


def test_the_setup_on_both_timeframes():
    h1 = hours(*DRIFT, *PAUSE, BREAK)
    m15 = [bar(11 * 3600 + i * 900, *row) for i, row in enumerate(TWICE)]
    [(base, back, entry)] = find_setups(h1, m15)
    assert (base.idx, back, entry) == (10, 3, 5)


def test_the_second_break_has_to_come_close_to_the_breakout():
    quiet = [(101.3, 101.6, 101.1, 101.4)] * 4  # back inside, two more hours with no break
    assert second_break(quarters(*TWICE[:4], *quiet, *TWICE[4:]), 0, BASE) is None
