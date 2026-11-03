"""The picture sent with a Telegram alert: the candles around the setup, the zone, and
the alert's marking — drawn here with matplotlib, in the chart window's colors, so the
alert reads without opening the app. An alert's trade (trade_plan.py) is drawn right of
its entry candle, the last one, as TradingView's long/short position tool: the target's
box green, the stop's red. Once the trade is over (trade_tracker.py), it's drawn from its
entry candle to the one that reached the target or the stop, which is marked. An example
from the past is drawn with the rest of its day after the entry (the user's ask, 2/11/26).

Candles sit one per slot with no gaps for weekends or closed hours, as on the chart.
Text stays Latin: matplotlib doesn't lay out Hebrew right to left."""
import io
from datetime import datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Rectangle
from mpl_toolkits.axes_grid1 import make_axes_locatable

from .candles import Candle
from .config import settings
from .trade_plan import TradePlan, first_hit

UP = "#26a69a"
DOWN = "#ef5350"
ZONE_FILL = (37 / 255, 120 / 255, 235 / 255, 0.16)
ZONE_LINE = (37 / 255, 120 / 255, 235 / 255, 0.6)
# The zone's (fill, edges) when it's drawn in gold, as the user draws it on TradingView.
GOLD = ((212 / 255, 160 / 255, 23 / 255, 0.22), (180 / 255, 130 / 255, 10 / 255, 0.8))
BREAK = "#ea580c"
TOUCH = "#9333ea"
LINE = "#f59e0b"
GRID = "#eceff3"
TEXT = "#374151"
TARGET = "#089981"
STOP = "#f23645"
TARGET_FILL = (8 / 255, 153 / 255, 129 / 255, 0.2)
STOP_FILL = (242 / 255, 54 / 255, 69 / 255, 0.2)
ENTRY = "#131722"
# Candles shown before the first marked one, and the fewest / most shown in all.
LEAD_BARS = 15
MIN_BARS = 80
MAX_BARS = 160
# The trade's box spans this share of the candles shown (at least PLAN_MIN_BARS slots), so
# its labels fit whatever the zoom.
PLAN_SHARE = 1 / 6
PLAN_MIN_BARS = 14
# The Stochastic RSI panel under the candles: its lines, its oversold / overbought levels.
STOCH_K = "#2962ff"
STOCH_D = "#ff6d00"
STOCH_LEVELS = (20, 80)


def rest_of_day(candles: Sequence[Candle], i: int) -> list[Candle]:
    """The candles after candle i on its day, in the alerts' time zone — what an example
    from the past is drawn with (draw's after)."""
    tz = ZoneInfo(settings.alert_timezone)
    day = datetime.fromtimestamp(candles[i].time, tz).date()
    out = []
    for c in candles[i + 1 :]:
        if datetime.fromtimestamp(c.time, tz).date() != day:
            break
        out.append(c)
    return out


def _index(candles: Sequence[Candle], iso: str) -> Optional[int]:
    ts = int(datetime.fromisoformat(iso).timestamp())
    return next((i for i, c in enumerate(candles) if c.time == ts), None)


def draw(
    ax,
    candles: Sequence[Candle],
    title: str,
    zone_bottom: Optional[float],
    zone_top: Optional[float],
    zone_start: Optional[str],
    direction: Optional[str] = None,
    breakout: Optional[str] = None,
    retests: Sequence[str] = (),
    touches: Sequence[str] = (),
    line: Sequence[float] = (),
    extra_zones: Sequence[tuple[float, float]] = (),
    time_format: str = "%d/%m %H:%M",
    min_bars: int = MIN_BARS,
    plan: Optional[TradePlan] = None,
    zone_colors: tuple = (ZONE_FILL, ZONE_LINE),
    plan_from: Optional[str] = None,
    closed: Optional[str] = None,
    closed_at: Optional[str] = None,
    plan_labels: bool = True,
    stoch: Optional[tuple[Sequence[float], Sequence[float]]] = None,
    arrow_color: str = BREAK,
    boxes: Sequence[tuple[str, str]] = (),
    box_color: str = BREAK,
    level: Optional[tuple[float, str]] = None,
    level_color: str = TOUCH,
    legend: Sequence[tuple[str, object]] = (),
    after: Sequence[Candle] = (),
) -> None:
    """The alert's chart, onto ax. The times are the alert's own ISO candle times; the
    zone is drawn from zone_start (or the left edge, if that's further back) onward, if
    there is one. line is an indicator (the EMA) aligned with candles. extra_zones are
    more (bottom, top) zones, drawn across the whole chart and always kept in view.
    time_format labels the time axis ("%d/%m" for daily candles); min_bars is the fewest
    candles shown, fewer for a setup a few days long. plan is the trade, entered on the
    last candle, or on plan_from's; closed ("target" | "stop") is the level the last
    candle reached — or closed_at's, the candles after it showing where price went on;
    plan_labels=False draws its boxes without the price labels. zone_colors
    are the zone's (fill, edges), GOLD for gold. stoch is the Stochastic RSI's (%K, %D),
    aligned with candles, drawn in a panel under them. arrow_color is the breakout arrow's.
    boxes are (first, last) candle times, each run of candles framed in box_color; level
    is a (price, from time) line in level_color; legend is (label, color) entries. after
    are the candles that came after the last one, for an example from the past: shown right
    of it, the plan's trade entered on that last candle and closed on the first of them that
    reached its target or stop, if one did; line and stoch may run on over them."""
    end = len(candles)  # the view is framed on the candles up to here, after added on the right
    if after:
        entry = candles[-1]
        hit = first_hit(plan, after) if plan else None
        candles = [*candles, *after]
        plan_from = plan_from or datetime.fromtimestamp(entry.time, timezone.utc).isoformat()
        if hit:
            closed, closed_at = hit[0], datetime.fromtimestamp(hit[1], timezone.utc).isoformat()
    times = [breakout, *retests, *touches, plan_from, *(t for box in boxes for t in box), level[1] if level else None]
    marks = [i for i in (_index(candles, t) for t in times if t) if i is not None]
    first = min(marks) if marks else end
    lo = max(0, min(first - LEAD_BARS, end - min_bars), end - MAX_BARS)
    shown = list(candles[lo:])
    n = len(shown)

    def at(iso: Optional[str]) -> Optional[int]:
        i = _index(candles, iso) if iso else None
        return None if i is None or i < lo else i - lo

    ax.set_facecolor("white")
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "left"):
        ax.spines[side].set_visible(False)
    for side in ("right", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.yaxis.tick_right()
    ax.tick_params(colors=TEXT, labelsize=8, length=0)

    span = max(c.high for c in shown) - min(c.low for c in shown)
    x0 = at(plan_from) if plan_from else None
    x0 = n - 1 if x0 is None else x0
    hit_x = at(closed_at) if closed_at else None
    hit_x = n - 1 if hit_x is None else hit_x
    plan_bars = max(PLAN_MIN_BARS, round(n * PLAN_SHARE), hit_x - x0) if plan else 0
    right = max(x0 + plan_bars, n - 1) + 2

    def draw_zone(x: int, bottom: float, top: float, fill=ZONE_FILL, edge=ZONE_LINE):
        ax.add_patch(Rectangle(
            (x - 0.5, bottom), right - x + 1, top - bottom, facecolor=fill, edgecolor="none", zorder=1,
        ))
        for y in (bottom, top):
            ax.plot([x - 0.5, right + 0.5], [y, y], color=edge, linewidth=1, zorder=1)

    has_zone = zone_bottom is not None and zone_top is not None
    if has_zone:
        draw_zone(at(zone_start) or 0, zone_bottom, zone_top, *zone_colors)
    for bottom, top in extra_zones:
        draw_zone(0, bottom, top)
    if line:
        ax.plot(range(len(line) - lo), line[lo:], color=LINE, linewidth=1.3, zorder=4)
    for first_t, last_t in boxes:
        x1, x2 = at(first_t), at(last_t)
        if x1 is None or x2 is None:
            continue
        run = shown[x1 : x2 + 1]
        pad = span * 0.01
        bottom, top = min(c.low for c in run) - pad, max(c.high for c in run) + pad
        ax.add_patch(Rectangle(
            (x1 - 0.5, bottom), x2 - x1 + 1, top - bottom, facecolor=to_rgba(box_color, 0.12), edgecolor=box_color,
            linewidth=1.4, zorder=1.8,
        ))
    if level:
        x = at(level[1])
        ax.plot([0 if x is None else x, right], [level[0], level[0]], color=level_color, linewidth=1.3, zorder=4)

    width = 0.7
    thinnest = (zone_top - zone_bottom) * 0.02 if has_zone else span * 0.002
    for x, c in enumerate(shown):
        color = UP if c.close >= c.open else DOWN
        ax.plot([x, x], [c.low, c.high], color=color, linewidth=0.9, zorder=2)
        body = max(abs(c.close - c.open), thinnest)
        ax.add_patch(Rectangle(
            (x - width / 2, min(c.open, c.close)), width, body, facecolor=color, edgecolor=color, zorder=3,
        ))

    gap = span * 0.03
    b = at(breakout)
    if b is not None:
        c = shown[b]
        up = direction != "down"
        ax.scatter(
            [b], [c.low - gap if up else c.high + gap], marker="^" if up else "v",
            s=90, color=arrow_color, zorder=5, edgecolors="white", linewidths=0.8,
        )
    for t in [*retests, *touches]:
        x = at(t)
        if x is None:
            continue
        c = shown[x]
        # Marked on the candle's side that reached the zone (or line): its low when it came down onto it.
        mid = (zone_top + zone_bottom) / 2 if has_zone else line[lo + x]
        ax.scatter(
            [x], [c.low - gap if abs(c.low - mid) < abs(c.high - mid) else c.high + gap], marker="o",
            s=55, color=TOUCH, zorder=5, edgecolors="white", linewidths=0.8,
        )

    if plan:
        mid = x0 + plan_bars / 2
        for edge, fill in ((plan.target, TARGET_FILL), (plan.stop, STOP_FILL)):
            ax.add_patch(Rectangle(
                (x0, min(plan.entry, edge)), plan_bars, abs(edge - plan.entry), facecolor=fill, edgecolor="none",
                zorder=1.5,
            ))
        ax.plot([x0, x0 + plan_bars], [plan.entry, plan.entry], color=ENTRY, linewidth=1, zorder=4)
        for label, edge, color in (("Target", plan.target, TARGET), ("Stop", plan.stop, STOP)):
            if closed == label.lower():
                label += " hit"
                ax.scatter([hit_x], [edge], marker="X", s=130, color=color, zorder=7, edgecolors="white", linewidths=1)
            if not plan_labels:
                continue
            away = 1 if edge > plan.entry else -1
            pct = abs(edge - plan.entry) / plan.entry * 100
            ax.annotate(
                f"{label}: {edge:g} ({pct:.2f}%)", xy=(mid, edge), xytext=(0, 4 * away), textcoords="offset points",
                ha="center", va="bottom" if away > 0 else "top", fontsize=8, color="white", zorder=6,
                bbox=dict(boxstyle="round,pad=0.3", facecolor=color, edgecolor="none"),
            )
        if plan_labels:
            ratio = round(abs(plan.target - plan.entry) / abs(plan.entry - plan.stop), 1)
            ax.annotate(
                f"Entry: {plan.entry:g} · R:R 1:{ratio:g}", xy=(mid, plan.entry), ha="center", va="center",
                fontsize=8, color="white", zorder=6, bbox=dict(boxstyle="round,pad=0.3", facecolor=ENTRY, edgecolor="none"),
            )

    if not closed:
        last = shown[-1].close
        ax.axhline(last, color=UP if last >= shown[-1].open else DOWN, linewidth=0.8, linestyle="--", zorder=4)
        ax.annotate(
            f"{last:g}", xy=(1, last), xycoords=("axes fraction", "data"), xytext=(4, 0),
            textcoords="offset points", va="center", fontsize=8, color="white",
            bbox=dict(boxstyle="square,pad=0.25", facecolor=UP if last >= shown[-1].open else DOWN, edgecolor="none"),
            annotation_clip=False,
        )

    tz = ZoneInfo(settings.alert_timezone)
    step = max(1, n // 8)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([datetime.fromtimestamp(shown[i].time, tz).strftime(time_format) for i in ticks])
    ax.set_xlim(-1, right)
    levels = [plan.stop, plan.target] if plan else []
    low = min([c.low for c in shown] + [bottom for bottom, _ in extra_zones] + levels)
    high = max([c.high for c in shown] + [top for _, top in extra_zones] + levels)
    ax.set_ylim(low - span * 0.08, high + span * 0.08)
    ax.set_title(title, loc="left", fontsize=12, color=TEXT, fontweight="bold")
    if legend:
        ax.legend(
            handles=[Patch(facecolor=color, edgecolor=color, label=label) for label, color in legend],
            loc="best", fontsize=8, framealpha=0.85,
        )

    if stoch:
        # The time axis moves to the panel, which shares it.
        osc = make_axes_locatable(ax).append_axes("bottom", size="28%", pad=0.15, sharex=ax)
        ax.tick_params(labelbottom=False)
        osc.set_facecolor("white")
        for side in ("top", "left"):
            osc.spines[side].set_visible(False)
        for side in ("right", "bottom"):
            osc.spines[side].set_color(GRID)
        osc.yaxis.tick_right()
        osc.tick_params(colors=TEXT, labelsize=8, length=0)
        osc.axhspan(*STOCH_LEVELS, color=GRID, alpha=0.6, zorder=0)
        for level in STOCH_LEVELS:
            osc.axhline(level, color=TEXT, linewidth=0.6, linestyle="--", alpha=0.5, zorder=1)
        k, d = stoch
        osc.plot(range(len(k) - lo), list(k[lo:]), color=STOCH_K, linewidth=1.1, zorder=3)
        osc.plot(range(len(d) - lo), list(d[lo:]), color=STOCH_D, linewidth=1.1, zorder=2)
        osc.set_ylim(-5, 105)
        osc.set_yticks(STOCH_LEVELS)
        osc.text(0.005, 0.95, "Stoch RSI 14 14 3 3", transform=osc.transAxes, fontsize=8, color=TEXT, va="top")


def _png(panels: Sequence[tuple[tuple, dict]], size: tuple[float, float]) -> bytes:
    fig = Figure(figsize=size, dpi=110, facecolor="white")
    FigureCanvasAgg(fig)
    for i, (args, kwargs) in enumerate(panels):
        draw(fig.add_subplot(1, len(panels), i + 1), *args, **kwargs)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def render(*args, **kwargs) -> bytes:
    """PNG of the alert's chart; draw()'s arguments, without ax."""
    return _png([(args, kwargs)], (10, 5.6))


def render_side_by_side(left: tuple[tuple, dict], right: tuple[tuple, dict]) -> bytes:
    """PNG of two charts side by side, each draw()'s (arguments, keyword arguments)
    without ax — one setup on two timeframes."""
    return _png([left, right], (16, 6))
