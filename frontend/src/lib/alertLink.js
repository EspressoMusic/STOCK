// A Telegram alert links to the scanner (and the desktop chart window, trainer.html)
// with its own marking (backend telegram.chart_window_url): ?symbol=&tf=&zone=bottom,top
// &from=, plus dir + breakout + retests or touches — candle times in unix seconds,
// comma-separated.

const ALERT_TIMEFRAMES = ["1h", "4h", "1d", "1w"];

// The alert in the page's address; null when the page wasn't opened from one.
export function readAlert() {
  const params = new URLSearchParams(window.location.search);
  const symbol = params.get("symbol")?.trim().toUpperCase();
  const timeframe = params.get("tf");
  const [bottom, top] = (params.get("zone") || "").split(",").map(Number);
  if (!symbol || !ALERT_TIMEFRAMES.includes(timeframe) || !(bottom < top)) return null;
  const times = (key) => (params.get(key) || "").split(",").map(Number).filter((t) => t > 0);
  return {
    symbol,
    timeframe,
    bottom,
    top,
    from: times("from")[0] ?? null,
    dir: params.get("dir") === "down" ? "down" : "up",
    breakout: times("breakout")[0] ?? null,
    retests: times("retests"),
    touches: times("touches"),
  };
}

// A plain address again, so a reload doesn't bring the alert back.
export function clearAlertFromUrl() {
  window.history.replaceState(null, "", window.location.pathname);
}

// The alert's zone and its candles, as ZonesPrimitive draws them; null if none of its
// candles are in the ones loaded.
export function alertMarking(alert, candles) {
  const idxOf = (time) => candles.findIndex((c) => c.time >= time);
  const mid = (alert.bottom + alert.top) / 2;
  const up = alert.dir === "up";
  const candleMarks = [];
  const mark = (time, text, tone, above) => {
    const idx = idxOf(time);
    if (idx === -1) return;
    const c = candles[idx];
    candleMarks.push({ idx, text, tone, above: above ?? (c.high + c.low) / 2 > mid });
  };
  if (alert.breakout) mark(alert.breakout, "פריצה", alert.dir, up);
  alert.retests.forEach((time, n) => mark(time, `ריטסט ${n + 1}`, alert.dir, !up));
  alert.touches.forEach((time, n) => mark(time, `נגיעה ${n + 1}`, "zone"));
  if (!candleMarks.length) return null;
  const fromIdx = alert.from ? idxOf(alert.from) : -1;
  const startIdx = fromIdx === -1 ? Math.min(...candleMarks.map((m) => m.idx)) : fromIdx;
  const zone = { id: "alert", bottom: alert.bottom, top: alert.top, startIdx, touches: alert.touches.length || null };
  return { zones: [zone], events: [], candleMarks };
}
