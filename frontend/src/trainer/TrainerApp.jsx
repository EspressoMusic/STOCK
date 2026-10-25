import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { getCandles, searchSymbols } from "../api";
import { alertMarking, clearAlertFromUrl, readAlert } from "../lib/alertLink";
import { computeEma } from "../lib/ema";
import { backtest, costFor, passedOn, STRATEGIES, TARGET_WEEKLY } from "../lib/strategies";
import { ThickCandlestickSeries } from "../lib/thickCandlestickSeries";
import { detectZonesFrom, PIVOT_BARS } from "../lib/zones";
import { ZonesPrimitive } from "../lib/zonesPrimitive";
import ScannerSidebar from "./ScannerSidebar";

// The desktop chart window (trainer.html): a chart, a symbol search, four timeframes and
// the bot's zone marking — automatic, or from a start line the user puts on the chart.
// The chart looks like the app's chart screen (ChartScanScreen).

// The chart's default colors per theme (the page itself follows index.css's data-theme).
// The user can change each of them from the colors panel.
const CHART_THEMES = {
  light: { bg: "#f5ecd8", ink: "#17140c", up: "#0ca30c", down: "#d03b3b", candleInk: "#000000", ema: "#000000" },
  dark: { bg: "#211b0e", ink: "#f5ead0", up: "#0ca30c", down: "#d03b3b", candleInk: "#f5ead0", ema: "#f5ead0" },
};
const COLOR_FIELDS = [
  { key: "bg", label: "רקע הגרף" },
  { key: "up", label: "נר עולה" },
  { key: "down", label: "נר יורד" },
  { key: "candleInk", label: "מסגרת ופתיל" },
  { key: "ema", label: "ממוצע נע" },
  { key: "ink", label: "טקסט וצירים" },
];
// Body colors of a strategy's entry and exit candles.
const ENTRY_COLOR = "#2f6fdf";
const EXIT_COLOR = "#f2b705";
const EMA_PERIOD = 50;
const VISIBLE_BARS = 140;
// Opened from an alert: the view starts this many candles before its first marked one,
// and shows at least MIN_ALERT_BARS.
const ALERT_LEAD_BARS = 20;
const MIN_ALERT_BARS = 60;
const TIMEFRAMES = [
  { value: "1h", label: "שעה" },
  { value: "4h", label: "4 שעות" },
  { value: "1d", label: "יום" },
  { value: "1w", label: "שבוע" },
];
const PREFS_KEY = "zone-trainer-prefs";
const MARK_MODES = [
  { value: "off", label: "בלי סימון" },
  { value: "auto", label: "סימון אוטומטי" },
  { value: "anchor", label: "פס התחלה" },
];
const MIN_TOUCHES = 2; // a price touched twice and turned from is a zone, on every timeframe
const STRONG_TOUCHES = 3; // a zone touched this often shows even when it isn't the nearest
const ZONE_TEXTS = { breakout: "פריצה", retest: "ריטסט" };
const EMPTY_MARKS = { zones: [], events: [] };
// Only the strategies that passed the profitability check (see lib/strategies.js).
const OFFERED_STRATEGIES = STRATEGIES.filter((s) => s.verified);

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function signedPct(value, digits = 1) {
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

// Candle index → body color for each trade's entry and exit candle (an open trade has no
// exit yet; a candle that both opens and closes a trade shows as an exit).
function tradeCandleColors(trades) {
  const colors = new Map();
  for (const t of trades) {
    if (!colors.has(t.entryIdx)) colors.set(t.entryIdx, ENTRY_COLOR);
    if (!t.open) colors.set(t.exitIdx, EXIT_COLOR);
  }
  return colors;
}

// Keeps the chart readable: only the zone price is in, the nearest zone above and below
// it and any zone touched STRONG_TOUCHES+ times, plus the latest breakout+retest.
function declutter({ zones, events }, price) {
  const inside = zones.filter((z) => z.bottom <= price && price <= z.top);
  const above = zones.filter((z) => z.bottom > price).sort((a, b) => a.bottom - b.bottom);
  const below = zones.filter((z) => z.top < price).sort((a, b) => b.top - a.top);
  const near = [...inside, ...above.slice(0, 1), ...below.slice(0, 1)];
  const strong = zones.filter((z) => z.touches >= STRONG_TOUCHES && !near.includes(z));
  const latest = events.reduce((best, ev) => (!best || ev.retestIdx > best.retestIdx ? ev : best), null);
  return { zones: [...near, ...strong], events: latest ? [latest] : [] };
}

function readPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

// lightweight-charts TickMarkType: 0 year, 1 month, 2 day of month, 3+ time of day.
function tickMarkFormatter(time, type) {
  const d = new Date(time * 1000);
  if (type === 0) return String(d.getFullYear());
  if (type === 1) return d.toLocaleDateString("he-IL", { month: "short" });
  if (type === 2) return d.toLocaleDateString("he-IL", { day: "numeric", month: "numeric" });
  return d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
}

function crosshairTimeFormatter(time) {
  return new Date(time * 1000).toLocaleString("he-IL", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function TrainerApp() {
  // Opened from a Telegram alert: its symbol and timeframe, with its marking instead of
  // the usual one until another symbol or marking is picked.
  const [alert, setAlert] = useState(readAlert);
  const [timeframe, setTimeframe] = useState(() => {
    const stored = alert?.timeframe || readPrefs().timeframe;
    return TIMEFRAMES.some((tf) => tf.value === stored) ? stored : "1h";
  });
  const [symbol, setSymbol] = useState(() => alert?.symbol || readPrefs().symbol || "");
  const [markMode, setMarkMode] = useState(() => {
    const stored = readPrefs().markMode;
    return MARK_MODES.some((m) => m.value === stored) ? stored : "auto";
  });
  // The alert's marking is only of its own timeframe; on another one the usual marking shows.
  const mode = alert && alert.symbol === symbol && alert.timeframe === timeframe ? "alert" : markMode;
  // Start-line candle times per symbol ({ AAPL: unixSeconds }), so the line lands on the
  // same moment on every timeframe and is still there next time.
  const [anchors, setAnchors] = useState(() => {
    const stored = readPrefs().anchors;
    return stored && typeof stored === "object" ? stored : {};
  });
  const anchorTime = anchors[symbol] ?? null;
  // true after tapping "פס התחלה" again: the next tap on the chart moves the line there.
  const [repositioning, setRepositioning] = useState(false);
  const placingAnchor = mode === "anchor" && (anchorTime == null || repositioning);
  const [draggingAnchor, setDraggingAnchor] = useState(false);
  // The line's x in px and the pane's size, or null while it isn't drawn.
  const [anchorPos, setAnchorPos] = useState(null);
  // The scanner sits closed behind a small tab on the side until it's opened.
  const [scannerOpen, setScannerOpen] = useState(() => readPrefs().scannerOpen === true);
  // The strategy toggle is hidden for now, so never restore a saved strategy.
  const [strategyId] = useState(null);
  const [theme, setTheme] = useState(() => (readPrefs().theme === "dark" ? "dark" : "light"));
  // The user's own chart colors, separately per theme ({ dark: { up: "#…" } }).
  const [customColors, setCustomColors] = useState(() => {
    const stored = readPrefs().colors;
    return stored && typeof stored === "object" ? stored : {};
  });
  const chartColors = useMemo(() => ({ ...CHART_THEMES[theme], ...customColors[theme] }), [theme, customColors]);
  const [colorsOpen, setColorsOpen] = useState(false);
  const colorsRef = useRef(null);
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [candles, setCandles] = useState(null);
  const [loadState, setLoadState] = useState(null); // null | "loading" | error message

  const containerRef = useRef(null);
  const seriesRef = useRef(null);
  const emaRef = useRef(null);
  const chartRef = useRef(null);
  const zonesRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({ timeframe, symbol, markMode, anchors, scannerOpen, strategy: strategyId, theme, colors: customColors })
      );
    } catch {
      /* private mode etc. — prefs just don't stick */
    }
  }, [timeframe, symbol, markMode, anchors, scannerOpen, strategyId, theme, customColors]);

  // A click outside the colors panel, or Escape, closes it.
  useEffect(() => {
    if (!colorsOpen) return;
    const onPointerDown = (e) => {
      if (!colorsRef.current?.contains(e.target)) setColorsOpen(false);
    };
    const onKeyDown = (e) => {
      if (e.key === "Escape") setColorsOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [colorsOpen]);

  // Before paint, so a dark window doesn't flash light first.
  useLayoutEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const strategy = OFFERED_STRATEGIES.find((s) => s.id === strategyId) || null;
  const strategyResult = useMemo(
    () => (strategy && candles?.length ? backtest(candles, strategy, costFor(symbol)) : null),
    [strategy, candles, symbol]
  );
  const stats = strategyResult?.stats;
  const openTrade = strategyResult?.trades.find((t) => t.open) || null;

  const setAnchorTime = useCallback((time) => setAnchors((all) => ({ ...all, [symbol]: time })), [symbol]);

  // The first candle at or after the line; a line past the last candle stays on it.
  const anchorIdx = useMemo(() => {
    if (mode !== "anchor" || anchorTime == null || !candles?.length) return null;
    const idx = candles.findIndex((c) => c.time >= anchorTime);
    return idx === -1 ? candles.length - 1 : idx;
  }, [mode, anchorTime, candles]);

  // The first and last candle on screen ({ first, last }; null until the chart reports
  // its range).
  const [viewBars, setViewBars] = useState(null);

  // The marking is of the screen only: the analysis uses just the candles on screen, as
  // if the last of them were today, and with a start line only those right of it. It also
  // gets the PIVOT_BARS candles before them, so a touch on the first candle on screen is
  // still a swing high/low — otherwise a zone vanishes when its touch nears the left edge.
  const marks = useMemo(() => {
    if (!candles?.length || mode === "off" || (mode === "anchor" && anchorIdx == null)) return EMPTY_MARKS;
    if (mode === "alert") return alertMarking(alert, candles) || EMPTY_MARKS;
    const last = clamp(viewBars?.last ?? candles.length - 1, 0, candles.length - 1);
    const first = Math.max(viewBars?.first ?? candles.length - VISIBLE_BARS, mode === "anchor" ? anchorIdx : 0, 0);
    if (first > last) return EMPTY_MARKS;
    const found = detectZonesFrom(candles.slice(0, last + 1), Math.max(first - PIVOT_BARS, 0), {
      minTouches: MIN_TOUCHES,
    });
    return declutter(found, candles[last].close);
  }, [candles, mode, alert, anchorIdx, viewBars]);
  const alertFirstIdx = marks.candleMarks?.length ? Math.min(...marks.candleMarks.map((m) => m.idx)) : null;

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSuggestions([]);
      return;
    }
    const id = setTimeout(() => {
      searchSymbols(trimmed)
        .then((data) => setSuggestions((data.results || []).slice(0, 8)))
        .catch(() => setSuggestions([]));
    }, 300);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    // Colors come from the chartColors effect below.
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        fontFamily: getComputedStyle(document.body).fontFamily,
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 6, tickMarkFormatter },
      localization: { locale: "he-IL", timeFormatter: crosshairTimeFormatter },
    });
    seriesRef.current = chart.addCustomSeries(new ThickCandlestickSeries(), {
      borderWidth: 2,
      wickWidth: 2,
      priceLineVisible: false,
    });
    emaRef.current = chart.addSeries(LineSeries, {
      lineWidth: 3,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    const zones = new ZonesPrimitive();
    seriesRef.current.attachPrimitive(zones);
    zonesRef.current = zones;
    chartRef.current = chart;
    return () => {
      chartRef.current = null;
      seriesRef.current = null;
      emaRef.current = null;
      zonesRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const c = chartColors;
    chartRef.current?.applyOptions({
      layout: { background: { color: c.bg }, textColor: c.ink },
      rightPriceScale: { borderColor: c.ink },
      timeScale: { borderColor: c.ink },
    });
    seriesRef.current?.applyOptions({ upColor: c.up, downColor: c.down, borderColor: c.candleInk, wickColor: c.candleInk });
    emaRef.current?.applyOptions({ color: c.ema });
  }, [chartColors]);

  const setChartColor = (key, value) => setCustomColors((all) => ({ ...all, [theme]: { ...all[theme], [key]: value } }));
  const resetChartColors = () => setCustomColors((all) => ({ ...all, [theme]: {} }));

  // Loads the symbol on screen whenever it or the timeframe changes; a newer load wins.
  useEffect(() => {
    if (!symbol) return;
    let stale = false;
    setLoadState("loading");
    getCandles(symbol, timeframe)
      .then((data) => {
        if (stale) return;
        if (!data.candles.length) throw new Error(`אין נרות עבור ${symbol}`);
        setCandles(data.candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        setLoadState(null);
      })
      .catch((err) => {
        if (stale) return;
        setLoadState(err instanceof TypeError ? "השרת לא זמין — צריך שה-backend ירוץ (פורט 8001)" : err.message);
      });
    return () => {
      stale = true;
    };
  }, [symbol, timeframe]);

  // The candles, with the picked strategy's entry and exit candles in their own colors.
  // Runs before the effect below, which frames the newly loaded candles.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !candles) return;
    const colors = strategyResult ? tradeCandleColors(strategyResult.trades) : null;
    series.setData(colors?.size ? candles.map((c, i) => (colors.has(i) ? { ...c, bodyColor: colors.get(i) } : c)) : candles);
  }, [candles, strategyResult]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || !candles) return;
    const lastClose = candles.length ? candles[candles.length - 1].close : 1;
    series.applyOptions({
      priceFormat: lastClose < 1 ? { type: "price", precision: 4, minMove: 0.0001 } : { type: "price", precision: 2, minMove: 0.01 },
    });
    emaRef.current.setData(computeEma(candles, EMA_PERIOD));
    series.priceScale().applyOptions({ autoScale: true });
    if (alertFirstIdx != null) {
      // Zoomed in on the alert's candles, so their labels don't run into each other.
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(Math.min(alertFirstIdx - ALERT_LEAD_BARS, candles.length - MIN_ALERT_BARS), 0),
        to: candles.length + 5,
      });
    } else if (candles.length > VISIBLE_BARS) {
      chart.timeScale().setVisibleLogicalRange({ from: candles.length - VISIBLE_BARS, to: candles.length + 5 });
    } else {
      chart.timeScale().fitContent();
    }
  }, [candles, alertFirstIdx]);

  // Follows the candles on screen as the chart pans and zooms (blank space past either
  // end counts as the end candle). Only a change of candle re-runs the marking.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !candles?.length) return;
    const timeScale = chart.timeScale();
    const lastIdx = candles.length - 1;
    const update = (range) => {
      const next = range && { first: clamp(Math.ceil(range.from), 0, lastIdx), last: clamp(Math.floor(range.to), 0, lastIdx) };
      setViewBars((prev) => (prev && next && prev.first === next.first && prev.last === next.last ? prev : next));
    };
    update(timeScale.getVisibleLogicalRange());
    timeScale.subscribeVisibleLogicalRangeChange(update);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(update);
  }, [candles]);

  useEffect(() => {
    zonesRef.current?.setState({
      candles: candles || [],
      zones: marks.zones,
      events: marks.events,
      candleMarks: marks.candleMarks || [],
      texts: ZONE_TEXTS,
    });
  }, [candles, marks]);

  // While placing, a tap on the chart (panning still works) puts the line on the left
  // edge of the tapped candle.
  useEffect(() => {
    const chart = chartRef.current;
    if (!placingAnchor || !chart || !candles?.length) return;
    const onClick = (param) => {
      if (param.logical == null) return;
      setAnchorTime(candles[clamp(Math.round(param.logical), 0, candles.length - 1)].time);
      setRepositioning(false);
    };
    chart.subscribeClick(onClick);
    return () => chart.unsubscribeClick(onClick);
  }, [placingAnchor, candles, setAnchorTime]);

  // Keeps the line on its candle's left edge as the chart pans and zooms.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || anchorIdx == null) {
      setAnchorPos(null);
      return;
    }
    const timeScale = chart.timeScale();
    const update = () => {
      const x = timeScale.logicalToCoordinate(anchorIdx);
      const { width, height } = chart.paneSize(0);
      setAnchorPos(x == null || !width ? null : { x: x - timeScale.options().barSpacing / 2, width, height });
    };
    update();
    timeScale.subscribeVisibleLogicalRangeChange(update);
    // The chart narrows and widens as the scanner opens and closes, not only on resize.
    timeScale.subscribeSizeChange(update);
    window.addEventListener("resize", update);
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(update);
      timeScale.unsubscribeSizeChange(update);
      window.removeEventListener("resize", update);
    };
  }, [anchorIdx, candles]);

  // Tapping "פס התחלה" again while it's on lets the next tap on the chart move the line.
  const pickMarkMode = (next) => {
    setRepositioning(next === "anchor" && mode === "anchor" && anchorTime != null && !repositioning);
    setMarkMode(next);
    dropAlert();
  };

  // Back to the usual marking, and a plain address so a reload doesn't bring the alert back.
  const dropAlert = () => {
    if (!alert) return;
    setAlert(null);
    clearAlertFromUrl();
  };

  // Dragging the line sideways moves it to the candle edge nearest the pointer.
  const moveAnchorTo = (clientX) => {
    const timeScale = chartRef.current?.timeScale();
    const rect = containerRef.current?.getBoundingClientRect();
    if (!timeScale || !rect || !candles?.length || !anchorPos) return;
    // The line sits on a candle's left edge; half a bar to the right is inside that candle.
    const x = clamp(clientX - rect.left, 0, anchorPos.width);
    const logical = timeScale.coordinateToLogical(x + timeScale.options().barSpacing / 2);
    if (logical == null) return;
    const idx = clamp(Math.round(logical), 0, candles.length - 1);
    if (idx !== anchorIdx) setAnchorTime(candles[idx].time);
  };

  const startAnchorDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDraggingAnchor(true);
  };

  const pickSymbol = (raw) => {
    const next = raw.trim().toUpperCase();
    if (!next) return;
    setQuery("");
    setSuggestions([]);
    setSymbol(next);
    if (next !== alert?.symbol) dropAlert();
  };

  return (
    <div className="trainer">
      <header className="trainer-header">
        <form
          className="trainer-search"
          onSubmit={(e) => {
            e.preventDefault();
            pickSymbol(suggestions[0]?.symbol || query);
          }}
        >
          <input
            type="text"
            placeholder={symbol || "סימול מניה"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {suggestions.length > 0 && (
            <div className="trainer-suggestions">
              {suggestions.map((m) => (
                <button key={m.symbol} type="button" onClick={() => pickSymbol(m.symbol)}>
                  <b>{m.symbol}</b>
                  {m.name && <span>{m.name}</span>}
                </button>
              ))}
            </div>
          )}
        </form>
        <div className="trainer-seg">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.value}
              type="button"
              className={timeframe === tf.value ? "active" : ""}
              onClick={() => setTimeframe(tf.value)}
            >
              {tf.label}
            </button>
          ))}
        </div>
        <div className="trainer-seg">
          {MARK_MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              className={mode === m.value ? "active" : ""}
              onClick={() => pickMarkMode(m.value)}
              title={m.value === "anchor" && mode === "anchor" ? "לחיצה נוספת — להזיז את הפס למקום אחר" : undefined}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="trainer-header-end">
          <div className="trainer-colors" ref={colorsRef}>
            <button
              type="button"
              className={`trainer-pill-btn${colorsOpen ? " active" : ""}`}
              onClick={() => setColorsOpen((open) => !open)}
            >
              🎨 צבעים
            </button>
            {colorsOpen && (
              <div className="trainer-colors-panel">
                {COLOR_FIELDS.map((f) => (
                  <label key={f.key}>
                    <input type="color" value={chartColors[f.key]} onChange={(e) => setChartColor(f.key, e.target.value)} />
                    {f.label}
                  </label>
                ))}
                <button type="button" className="trainer-colors-reset" onClick={resetChartColors}>
                  איפוס לצבעים הרגילים
                </button>
                <small>נשמר בנפרד למצב {theme === "dark" ? "כהה" : "בהיר"}</small>
              </div>
            )}
          </div>
          <button
            type="button"
            className="trainer-pill-btn"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          >
            {theme === "dark" ? "☀️ מצב בהיר" : "🌙 מצב כהה"}
          </button>
        </div>
      </header>

      <div className="trainer-side">
        <button
          type="button"
          className={`trainer-side-tab${scannerOpen ? " active" : ""}`}
          onClick={() => setScannerOpen((open) => !open)}
          title={scannerOpen ? "לסגור את הסורק" : "לפתוח את סורק המניות"}
        >
          סורק
        </button>
        <ScannerSidebar open={scannerOpen} symbol={symbol} onPick={pickSymbol} />
      </div>

      <div className="trainer-chart-wrap" dir="ltr" style={{ "--chart-bg": chartColors.bg }}>
        <div ref={containerRef} className="trainer-chart" />
        {stats && (
          <div className="trainer-strategy" dir="rtl">
            <p>
              <b>{strategy.short}</b> · {stats.trades} עסקאות
              {stats.winRate != null && ` · ${Math.round(stats.winRate * 100)}% מנצחות`}
            </p>
            <p>
              <span className={stats.weeklyReturn >= TARGET_WEEKLY ? "good" : "bad"}>
                {signedPct(stats.weeklyReturn, 2)} לשבוע
              </span>
              {` · קנה והחזק ${signedPct(stats.holdWeekly, 2)}`}
            </p>
            {openTrade && (
              <p>
                פתוחה: {openTrade.side === 1 ? "לונג" : "שורט"} ({signedPct(openTrade.pct)})
              </p>
            )}
            <p className="trainer-strategy-legend">
              <i style={{ background: ENTRY_COLOR }} />
              נר כניסה
              <i style={{ background: EXIT_COLOR }} />
              נר יציאה
            </p>
            <small>
              {passedOn(strategy, symbol, timeframe)
                ? `עברה את בדיקת הרווחיות ב${strategy.verified.where}`
                : `לא עברה את בדיקת הרווחיות כאן — רק ב${strategy.verified.where}`}
            </small>
          </div>
        )}
        {anchorPos && anchorPos.x > 0 && (
          <span
            className="chart-anchor-shade"
            style={{ width: `${Math.min(anchorPos.x, anchorPos.width)}px`, height: `${anchorPos.height}px` }}
          />
        )}
        {/* Stays mounted (pinned to the edge) while dragged, so it keeps the pointer. */}
        {anchorPos && (draggingAnchor || (anchorPos.x >= 0 && anchorPos.x <= anchorPos.width)) && (
          <div
            className={`chart-anchor-line${draggingAnchor ? " is-dragging" : ""}`}
            style={{ left: `${clamp(anchorPos.x, 0, anchorPos.width)}px`, height: `${anchorPos.height}px` }}
            onPointerDown={startAnchorDrag}
            onPointerMove={(e) => draggingAnchor && moveAnchorTo(e.clientX)}
            onPointerUp={() => setDraggingAnchor(false)}
            onPointerCancel={() => setDraggingAnchor(false)}
            onLostPointerCapture={() => setDraggingAnchor(false)}
          >
            <span className="chart-anchor-label">⟷ התחלה</span>
          </div>
        )}
        {mode === "alert" && !loadState && (
          <p className="chart-anchor-hint trainer-alert-hint" dir="rtl">
            הסימון מההתראה בטלגרם
            <button type="button" onClick={dropAlert} title="לחזור לסימון הרגיל">
              ✕
            </button>
          </p>
        )}
        {placingAnchor && symbol && !loadState && (
          <p className="chart-anchor-hint" dir="rtl">לחץ על הגרף במקום שממנו יתחיל הניתוח</p>
        )}
        {(!symbol || loadState) && (
          <div className="trainer-overlay" dir="rtl">
            {!symbol && <p>בחר מניה למעלה או מהסורק</p>}
            {loadState === "loading" && <p>טוען נרות...</p>}
            {loadState && loadState !== "loading" && <p className="trainer-error">{loadState}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
