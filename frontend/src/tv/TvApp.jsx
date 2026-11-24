import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import {
  createPriceAlert, deletePriceAlert, getCandles, getLatestScan, getLatestStochExtremeScan, getLatestTripleTouchScan,
  getLatestZoneTouchScan, getLivePrice, getMyTrades, getPriceAlerts, getQuote, getSignals, runScan, runStochExtremeScan, runTripleTouchScan,
  runZoneTouchScan, searchSymbols, signalPictureUrl,
} from "../api";
import { computeEma } from "../lib/ema";
import { BREAK_LOOKBACK, findReturnLevels, MAX_RETURN_BARS, MIN_RUN } from "../lib/returnLevels";
import { ReturnLevelsPrimitive } from "../lib/returnLevelsPrimitive";
import { detectZonesFrom, PIVOT_BARS } from "../lib/zones";
import { ThickCandlestickSeries } from "../lib/thickCandlestickSeries";
import { ZonesPrimitive } from "../lib/zonesPrimitive";

// The TradingView-style chart panel (tv.html): TradingView's layout — symbol search,
// timeframes, date ranges, a watchlist — with only two markings of the bot's own on the
// chart: strong zones and break-and-retest. Desktop only, like trainer.html.

// Light is the user's own TradingView chart: gray, a rising candle a strong matte blue, a falling one
// soft black, every candle's outline and wick and EMA 50 a strong black, and the price and time
// scales a darker gray, the top bar's (14/11/26). Dark stays TradingView's, with its orange EMA.
const CHART_THEMES = {
  dark: {
    bg: "#131722", axisBg: "#131722", text: "#d1d4dc", crosshair: "#758696", label: "#363a45",
    up: "#089981", down: "#f23645", edge: null, ema: "#ff9800",
  },
  light: {
    bg: "#787b86", axisBg: "#63666f", text: "#16181e", crosshair: "#2f323b", label: "#363a45",
    up: "#2c5aa0", down: "#4a4a4a", edge: "#000000", ema: "#000000",
  },
};

// The chart's own background is what its scales show, so it gets the scales' color and this
// paints the candles' area in the chart's.
class PaneFill {
  color = null;

  constructor() {
    const renderer = {
      draw: (target) =>
        target.useBitmapCoordinateSpace(({ context, bitmapSize }) => {
          if (!this.color) return;
          context.fillStyle = this.color;
          context.fillRect(0, 0, bitmapSize.width, bitmapSize.height);
        }),
    };
    this._paneViews = [{ zOrder: () => "bottom", renderer: () => renderer }];
  }

  attached({ requestUpdate }) {
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._requestUpdate = null;
  }

  paneViews() {
    return this._paneViews;
  }

  setColor(color) {
    this.color = color;
    this._requestUpdate?.();
  }
}

// The live price (24/11/26): TradingView's dotted line across the chart at the last price,
// colored as the last candle and moved every few seconds (LIVE_EVERY_MS); on the price scale,
// the price in an outlined box with the time left until the candle closes under it, like the
// user's TradingView (screenshot 24/11/26).
class LivePriceLine {
  _series = null;
  _state = null; // { price, color, precision, closeAt (unix seconds | null), text, axisBg }
  _timer = null;

  constructor() {
    const pane = {
      draw: (target) => target.useMediaCoordinateSpace(({ context, mediaSize }) => this._drawLine(context, mediaSize.width)),
    };
    const axis = {
      draw: (target) => target.useMediaCoordinateSpace(({ context, mediaSize }) => this._drawLabel(context, mediaSize.width)),
    };
    this._paneViews = [{ zOrder: () => "top", renderer: () => pane }];
    this._axisViews = [{ zOrder: () => "top", renderer: () => axis }];
  }

  attached({ series, requestUpdate }) {
    this._series = series;
    this._requestUpdate = requestUpdate;
    // The clock ticks every second.
    this._timer = setInterval(() => this._state?.closeAt && this._requestUpdate?.(), 1000);
  }

  detached() {
    clearInterval(this._timer);
    this._series = null;
    this._requestUpdate = null;
  }

  paneViews() {
    return this._paneViews;
  }

  priceAxisPaneViews() {
    return this._axisViews;
  }

  set(state) {
    this._state = state;
    this._requestUpdate?.();
  }

  _y() {
    if (!this._series || !this._state) return null;
    return this._series.priceToCoordinate(this._state.price);
  }

  _drawLine(ctx, width) {
    const y = this._y();
    if (y == null) return;
    const lineY = Math.round(y) + 0.5;
    ctx.save();
    ctx.strokeStyle = this._state.color;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(0, lineY);
    ctx.lineTo(width, lineY);
    ctx.stroke();
    ctx.restore();
  }

  _drawLabel(ctx, width) {
    const y = this._y();
    if (y == null) return;
    const { price, precision, closeAt, text, axisBg } = this._state;
    const left = closeAt != null ? closeAt - Date.now() / 1000 : 0;
    const clock = left > 0 ? countdown(left) : null;
    const top = Math.round(y) - 10;
    const height = clock ? 36 : 20;
    ctx.save();
    ctx.fillStyle = axisBg;
    ctx.fillRect(1, top, width - 2, height);
    ctx.strokeStyle = text;
    ctx.lineWidth = 1;
    ctx.strokeRect(1.5, top + 0.5, width - 3, height - 1);
    ctx.fillStyle = text;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `700 12px ${FONT}`;
    ctx.fillText(price.toFixed(precision), width / 2, top + 10.5);
    if (clock) {
      ctx.font = `600 11px ${FONT}`;
      ctx.fillText(clock, width / 2, top + 27);
    }
    ctx.restore();
  }
}

// "45:09", "03:12:45", "2d 7h": the time left until a candle closes.
function countdown(seconds) {
  const two = (n) => String(n).padStart(2, "0");
  const days = Math.floor(seconds / DAY);
  const hours = Math.floor((seconds % DAY) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (days > 0) return `${days}d ${hours}h`;
  return hours > 0 ? `${two(hours)}:${two(minutes)}:${two(secs)}` : `${two(minutes)}:${two(secs)}`;
}

const DAY = 86400;
const INTRADAY_STEP = { "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400 };
// The hour, New York time, a market's trading day ends; none for crypto (UTC days, 24/7).
const DAY_END_NY = { stock: 16, forex: 17, futures: 17, crypto: null };

function marketOf(symbol) {
  if (/=X$/.test(symbol)) return "forex";
  if (/=F$/.test(symbol)) return "futures";
  if (/-(USD|USDT|EUR|BTC|ETH)$/.test(symbol)) return "crypto";
  return "stock";
}

const NY_PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hourCycle: "h23",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
});

// A unix time as New York's clock shows it, read as if it were UTC.
function nyClock(time) {
  const p = Object.fromEntries(NY_PARTS.formatToParts(new Date(time * 1000)).map((x) => [x.type, x.value]));
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) / 1000;
}

// The unix time of `hour`:00 New York time on a date (given as that date's UTC midnight).
function nyHour(day, hour) {
  const guess = day + hour * 3600;
  return guess + (guess - nyClock(guess));
}

// When the candle starting at `time` closes, or null when unknown. Daily and longer candles
// start at midnight of their date (in New York or UTC), so their date is that of time + 12h.
// Holidays aren't known: a closed market just shows no clock, since its time has passed.
function candleCloseAt(symbol, timeframe, time) {
  const market = marketOf(symbol);
  const endHour = DAY_END_NY[market];
  const dayEnd = (day) => (endHour == null ? day + DAY : nyHour(day, endHour));
  const step = INTRADAY_STEP[timeframe];
  if (step) {
    const start = time - (time % 60); // Yahoo stamps a candle's live point to the second
    const close = start + step;
    if (endHour == null) return close;
    const nyDay = Math.floor(nyClock(start) / DAY) * DAY;
    const end = dayEnd(nyDay);
    // Stocks stop at the close every day, forex and futures on Fridays.
    const friday = new Date(nyDay * 1000).getUTCDay() === 5;
    return market === "stock" || (friday && start < end) ? Math.min(close, end) : close;
  }
  const day = Math.floor((time + 12 * 3600) / DAY) * DAY;
  if (timeframe === "1d") return dayEnd(day);
  if (timeframe === "1w") return endHour == null ? day + 7 * DAY : dayEnd(day + 4 * DAY);
  if (timeframe === "1mo") {
    const d = new Date(day * 1000);
    const next = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) / 1000;
    if (endHour == null) return next;
    let last = next - DAY;
    while ([0, 6].includes(new Date(last * 1000).getUTCDay())) last -= DAY;
    return dayEnd(last);
  }
  return null;
}

const ALERT_COLOR = "#d35400";

// The price alerts' dashed lines, from the pane's left edge to the last candle: a price line
// would run on to the right edge (15/11/26). Their prices on the price scale stay price lines.
class AlertLines {
  _chart = null;
  _series = null;
  _prices = [];
  _lastIdx = null;

  constructor() {
    const renderer = { draw: (target) => target.useMediaCoordinateSpace(({ context }) => this._draw(context)) };
    this._paneViews = [{ zOrder: () => "top", renderer: () => renderer }];
  }

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  paneViews() {
    return this._paneViews;
  }

  set(prices, lastIdx) {
    this._prices = prices;
    this._lastIdx = lastIdx;
    this._requestUpdate?.();
  }

  _draw(ctx) {
    if (!this._chart || !this._series || this._lastIdx == null) return;
    const timeScale = this._chart.timeScale();
    const last = timeScale.logicalToCoordinate(this._lastIdx);
    if (last == null) return;
    const x2 = last + timeScale.options().barSpacing / 2;
    ctx.save();
    ctx.strokeStyle = ALERT_COLOR;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    for (const price of this._prices) {
      const y = this._series.priceToCoordinate(price);
      if (y == null) continue;
      ctx.beginPath();
      ctx.moveTo(0, Math.round(y) + 0.5);
      ctx.lineTo(x2, Math.round(y) + 0.5);
      ctx.stroke();
    }
    ctx.restore();
  }
}

// The zone of one of the user's own trades (the Trades box): a grey box like theirs on
// TradingView, from the candle the zone began in to the last candle.
class TradeZone {
  _chart = null;
  _series = null;
  _zone = null; // { bottom, top, fromIdx, lastIdx }

  constructor() {
    const renderer = { draw: (target) => target.useMediaCoordinateSpace(({ context }) => this._draw(context)) };
    this._paneViews = [{ zOrder: () => "bottom", renderer: () => renderer }];
  }

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  paneViews() {
    return this._paneViews;
  }

  set(zone) {
    this._zone = zone;
    this._requestUpdate?.();
  }

  _draw(ctx) {
    const z = this._zone;
    if (!this._chart || !this._series || !z) return;
    const timeScale = this._chart.timeScale();
    const half = timeScale.options().barSpacing / 2;
    const from = timeScale.logicalToCoordinate(z.fromIdx);
    const to = timeScale.logicalToCoordinate(z.lastIdx);
    const top = this._series.priceToCoordinate(z.top);
    const bottom = this._series.priceToCoordinate(z.bottom);
    if (from == null || to == null || top == null || bottom == null) return;
    const x1 = Math.round(from - half);
    const x2 = Math.round(to + half);
    const y1 = Math.round(Math.min(top, bottom));
    const y2 = Math.max(Math.round(Math.max(top, bottom)), y1 + 2);
    ctx.save();
    ctx.fillStyle = "rgba(60, 62, 70, 0.35)";
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeStyle = "rgba(35, 37, 44, 0.9)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x1, y1 + 0.5);
    ctx.lineTo(x2, y1 + 0.5);
    ctx.moveTo(x1, y2 - 0.5);
    ctx.lineTo(x2, y2 - 0.5);
    ctx.stroke();
    ctx.restore();
  }
}

const FONT = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
const LOCALE = "en-GB"; // English, 24-hour clock
const TIMEFRAMES = [
  { value: "15m", label: "15m", title: "15 minutes" },
  { value: "1h", label: "1h", title: "1 hour" },
  { value: "4h", label: "4h", title: "4 hours" },
  { value: "1d", label: "D", title: "1 day" },
  { value: "1w", label: "W", title: "1 week" },
  { value: "1mo", label: "M", title: "1 month" },
];
const DEFAULT_WATCHLIST = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GC=F", "NQ=F", "AAPL", "NVDA", "TSLA", "SOXL", "BTC-USD"];
const PREFS_KEY = "tv-panel-prefs";
const VISIBLE_BARS = 140;
const RIGHT_OFFSET = 8;
const STRONG_TOUCHES = 3; // a zone touched this often is a strong zone
const ZONE_TEXTS = { breakout: "Break", retest: "Retest" };
// The candle box's sliders: outline + wick in px, body as % of the space between candles.
const CANDLE_EDGE = { min: 1, max: 8, initial: 3 };
const CANDLE_BODY = { min: 30, max: 95, initial: 80 };
const EMA_PERIOD = 50;
const EMPTY_MARKS = { zones: [], events: [] };
const SIGNAL_KINDS = [
  { value: "weekly", label: "Weekly" },
  { value: "day", label: "Day trade" },
  { value: "forex", label: "Forex" },
];
const QUOTES_EVERY_MS = 60_000;
const CANDLES_EVERY_MS = 60_000;
const LIVE_EVERY_MS = 3_000; // the live price on the chart (LivePriceLine)
const TRADE_LEAD_BARS = 30; // candles shown before a trade's zone began

// The movers scans store naive UTC datetimes, so they serialize without a zone suffix.
function asUtc(iso) {
  if (!iso || /(Z|[+-]\d{2}:?\d{2})$/.test(iso)) return iso;
  return `${iso}Z`;
}

function moversScan(id, label, field) {
  return {
    id,
    label,
    latest: () => getLatestScan({ direction: id }),
    run: () => runScan({ direction: id }),
    toView: (data) => ({
      scannedAt: asUtc(data.created_at),
      rows: data.results.map((s) => ({
        symbol: s.symbol,
        badge: `${signed(s[field], 2)}%`,
        tone: s[field] >= 0 ? "up" : "down",
        price: s.price,
      })),
    }),
  };
}

// The desktop chart window's scanner (trainer/ScannerSidebar.jsx), in English. autoRun: the
// slow technical scans start on their own when nothing is cached yet; the movers wait for Scan.
const SCANS = [
  moversScan("losers", "Losers", "change_percent"),
  moversScan("gainers", "Gainers", "change_percent"),
  moversScan("broken", "Broken", "fifty_two_week_change_percent"),
  {
    id: "triple-touch",
    label: "Shilo",
    autoRun: true,
    latest: () => getLatestTripleTouchScan("stocks"),
    run: () => runTripleTouchScan("stocks"),
    toView: (data) => ({
      scannedAt: data.scanned_at,
      rows: data.results.map((c) => ({
        symbol: c.symbol,
        badge: `${c.direction === "long" ? "Long" : "Short"} ×${c.touch_count}`,
        tone: c.direction === "long" ? "up" : "down",
        price: c.price,
      })),
    }),
  },
  {
    id: "stoch-extreme",
    label: "Shilo 2",
    autoRun: true,
    latest: getLatestStochExtremeScan,
    run: runStochExtremeScan,
    toView: (data) => ({
      scannedAt: data.scanned_at,
      rows: data.stocks.map((c) => ({
        symbol: c.symbol,
        badge: c.signal === "overbought" ? "Overbought" : "Oversold",
        tone: "",
        price: c.price,
      })),
    }),
  },
  {
    // Forex pairs on the 4h chart: a zone price turned at 3+ times, and the last candle closed inside it.
    id: "zone-touch",
    label: "3 touches",
    autoRun: true,
    latest: getLatestZoneTouchScan,
    run: runZoneTouchScan,
    toView: (data) => ({
      scannedAt: data.scanned_at,
      rows: data.results.map((c) => ({ symbol: c.symbol, badge: `In zone ×${c.touch_count}`, tone: "", price: c.price })),
    }),
  },
];

function scanError(err) {
  return err instanceof TypeError ? "Server unavailable — the backend must be running (port 8001)" : err.message;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function readPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

// Yahoo symbols the way TradingView writes them: EURUSD=X → EURUSD, GC=F → GC1!, ^GSPC → GSPC.
function shownSymbol(symbol) {
  return symbol.replace(/=X$/, "").replace(/=F$/, "1!").replace(/^\^/, "").replace(/-USD$/, "USD");
}

function precisionFor(symbol, price) {
  if (/=X$/.test(symbol)) return price >= 20 ? 3 : 4;
  return price < 1 ? 4 : 2;
}

function signed(value, digits) {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(digits)}`;
}

// lightweight-charts TickMarkType: 0 year, 1 month, 2 day of month, 3+ time of day.
function tickMarkFormatter(time, type) {
  const d = new Date(time * 1000);
  if (type === 0) return String(d.getFullYear());
  if (type === 1) return d.toLocaleDateString(LOCALE, { month: "short" });
  if (type === 2) return String(d.getDate());
  return d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

function crosshairTime(time) {
  return new Date(time * 1000).toLocaleString(LOCALE, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function signalTime(iso) {
  return new Date(iso).toLocaleString(LOCALE, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function crosshairDate(time) {
  return new Date(time * 1000).toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short", year: "2-digit" });
}

// The candle a trade's zone began in: the last one that opened at or before its start.
function tradeStartIdx(list, trade) {
  const start = Date.parse(trade.start) / 1000;
  let idx = 0;
  while (idx + 1 < list.length && list[idx + 1].time <= start) idx += 1;
  return idx;
}

// The zone as the chart shows prices, e.g. 1.6287 – 1.634.
function tradeZoneText(trade) {
  const digits = precisionFor(trade.symbol, trade.top) + 1;
  const fmt = (p) => String(Number(p.toFixed(digits)));
  return `${fmt(trade.bottom)} – ${fmt(trade.top)}`;
}

const ICONS = {
  search: (
    <svg viewBox="0 0 18 18" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="7.5" cy="7.5" r="5" />
      <path d="M11.3 11.3 16 16" />
    </svg>
  ),
  zones: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.3">
      <rect x="2.5" y="4" width="15" height="4" rx="1" fill="currentColor" fillOpacity="0.25" />
      <rect x="2.5" y="12" width="15" height="4" rx="1" fill="currentColor" fillOpacity="0.25" />
    </svg>
  ),
  breaks: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
      <path d="M1.5 10.5h17" strokeDasharray="2 2" />
      <path d="M2 16.5 6.5 13.5 10 5 13 10.2 18 3" />
    </svg>
  ),
  ema: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <path d="M2 15c4-1 5-7 9-8s5 1 7-2" />
    </svg>
  ),
  returns: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
      <path d="M2 5.5h16" strokeDasharray="2 2" />
      <path d="M3 5.5 7 15l4-3.5 5-6" />
    </svg>
  ),
  indicators: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 14.5c2.5 0 3-9 6-9s3.5 9 6 9 2.5-4 4-4" />
    </svg>
  ),
  candle: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.2">
      <path d="M10 1.5v4M10 14.5v4" />
      <rect x="5.5" y="5.5" width="9" height="9" rx="0.5" />
    </svg>
  ),
  check: (
    <svg viewBox="0 0 18 18" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m4 9.5 3.2 3.2L14 5.5" />
    </svg>
  ),
  list: (
    <svg viewBox="0 0 22 22" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M8 6h10M8 11h10M8 16h10" />
      <circle cx="4.5" cy="6" r="1" fill="currentColor" />
      <circle cx="4.5" cy="11" r="1" fill="currentColor" />
      <circle cx="4.5" cy="16" r="1" fill="currentColor" />
    </svg>
  ),
  bell: (
    <svg viewBox="0 0 22 22" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5.5 15.5V10a5.5 5.5 0 0 1 11 0v5.5l1.5 2H4z" />
      <path d="M9 19.5a2 2 0 0 0 4 0" />
    </svg>
  ),
  gallery: (
    <svg viewBox="0 0 22 22" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
      <rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1" />
      <rect x="12" y="3.5" width="6.5" height="6.5" rx="1" />
      <rect x="3.5" y="12" width="6.5" height="6.5" rx="1" />
      <rect x="12" y="12" width="6.5" height="6.5" rx="1" />
    </svg>
  ),
  trades: (
    <svg viewBox="0 0 22 22" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="9" width="16" height="6" rx="1" fill="currentColor" fillOpacity="0.25" />
      <path d="M8 3.5v13M15 6v13" />
    </svg>
  ),
  scanner: (
    <svg viewBox="0 0 22 22" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="6.5" />
      <path d="m15 15 4.5 4.5M6.5 11.5l2-2.5 2 1.5 2.5-3.5" />
    </svg>
  ),
  alarm: (
    <svg viewBox="0 0 22 22" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="12" r="6.5" />
      <path d="M11 8.5V12l2.2 1.6M3.5 5.5l3-2.5M18.5 5.5l-3-2.5" />
    </svg>
  ),
  plus: (
    <svg viewBox="0 0 18 18" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M9 3v12M3 9h12" />
    </svg>
  ),
  close: (
    <svg viewBox="0 0 18 18" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="m4 4 10 10M14 4 4 14" />
    </svg>
  ),
};

// TradingView's symbol search dialog: type, then Enter or a click picks; ↑/↓ move.
function SymbolSearch({ initial, title, onPick, onClose }) {
  const [text, setText] = useState(initial);
  const [results, setResults] = useState([]);
  const [active, setActive] = useState(0);
  // Yahoo's search takes a few seconds; until it answers the list says so.
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    const trimmed = text.trim();
    setSearching(!!trimmed);
    if (!trimmed) {
      setResults([]);
      return;
    }
    let stale = false;
    const id = setTimeout(() => {
      searchSymbols(trimmed)
        .then((data) => {
          if (stale) return;
          setResults(data.results || []);
          setActive(0);
        })
        .catch(() => !stale && setResults([]))
        .finally(() => !stale && setSearching(false));
    }, 250);
    return () => {
      stale = true;
      clearTimeout(id);
    };
  }, [text]);

  const onKeyDown = (e) => {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => clamp(i + (e.key === "ArrowDown" ? 1 : -1), 0, Math.max(results.length - 1, 0)));
    } else if (e.key === "Enter") {
      const picked = results[active]?.symbol || text.trim();
      if (picked) onPick(picked.toUpperCase());
    }
  };

  return (
    <div className="tv-modal-shade" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="tv-modal" role="dialog" aria-label={title}>
        <div className="tv-modal-head">
          <span>{title}</span>
          <button type="button" className="tv-icon-btn" onClick={onClose} title="Close">
            {ICONS.close}
          </button>
        </div>
        <label className="tv-modal-input">
          {ICONS.search}
          <input autoFocus value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown} placeholder="Symbol, e.g. AAPL or EURUSD=X" />
        </label>
        <div className="tv-modal-results">
          {results.map((m, i) => (
            <button
              key={m.symbol}
              type="button"
              className={`tv-result${i === active ? " active" : ""}`}
              onPointerEnter={() => setActive(i)}
              onClick={() => onPick(m.symbol.toUpperCase())}
            >
              <b>{shownSymbol(m.symbol)}</b>
              <span className="tv-result-name">{m.name}</span>
              <span className="tv-result-meta">{[m.type, m.exchange].filter(Boolean).join(" · ")}</span>
            </button>
          ))}
          {text.trim() && !results.length && (
            <p className="tv-modal-empty">{searching ? "Searching…" : `Enter — open ${text.trim().toUpperCase()}`}</p>
          )}
        </div>
      </div>
    </div>
  );
}

// A top bar box: a click outside it or Escape closes it. Clicks in the symbol search leave
// it open.
function useMenuBox() {
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e) =>
      !boxRef.current?.contains(e.target) && !e.target.closest?.(".tv-modal-shade") && setOpen(false);
    const onKeyDown = (e) => e.key === "Escape" && !document.querySelector(".tv-modal-shade") && setOpen(false);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return [open, setOpen, boxRef];
}

// The candle thickness box (14/11/26): the outline + wick thickness and the body's width.
function CandleMenu({ edge, body, onEdge, onBody }) {
  const [open, setOpen, boxRef] = useMenuBox();
  const sliders = [
    { key: "edge", name: "Outline", ...CANDLE_EDGE, value: edge, onChange: onEdge, unit: "px" },
    { key: "body", name: "Width", ...CANDLE_BODY, value: body, onChange: onBody, unit: "%" },
  ];

  return (
    <div className="tv-ind" ref={boxRef}>
      <button type="button" className={`tv-btn${open ? " active" : ""}`} onClick={() => setOpen((o) => !o)} title="Candle thickness">
        {ICONS.candle}
      </button>
      {open && (
        <div className="tv-ind-menu tv-candle-menu">
          {sliders.map((s) => (
            <label key={s.key} className="tv-candle-row">
              <span>{s.name}</span>
              <input
                type="range"
                min={s.min}
                max={s.max}
                value={s.value}
                onChange={(e) => s.onChange(Number(e.target.value))}
              />
              <b>
                {s.value}
                {s.unit}
              </b>
            </label>
          ))}
          <button
            type="button"
            className="tv-candle-reset"
            onClick={() => {
              onEdge(CANDLE_EDGE.initial);
              onBody(CANDLE_BODY.initial);
            }}
          >
            Reset
          </button>
        </div>
      )}
    </div>
  );
}

// TradingView's Indicators button: one box with every marking, a click turns one on or off.
function IndicatorsMenu({ items }) {
  const [open, setOpen, boxRef] = useMenuBox();

  return (
    <div className="tv-ind" ref={boxRef}>
      <button type="button" className={`tv-btn tv-btn-text${open ? " active" : ""}`} onClick={() => setOpen((o) => !o)} title="Indicators">
        {ICONS.indicators}
        Indicators
      </button>
      {open && (
        <div className="tv-ind-menu" role="menu">
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              role="menuitemcheckbox"
              aria-checked={it.on}
              className={`tv-ind-row${it.on ? " on" : ""}`}
              onClick={it.toggle}
              title={it.title}
            >
              <span className="tv-ind-check">{it.on && ICONS.check}</span>
              {it.icon}
              {it.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// The right side as on TradingView (the user's ask, 23/11/26): an icon rail on the window's
// edge and a panel docked beside it, the chart narrowing next to it. A click on an icon opens
// its panel, a second click closes it; the panel stays open while the chart is used.
const SIDE_PANELS = [
  { key: "watchlist", title: "Watchlist", icon: "list" },
  { key: "scanner", title: "Stock scanner", icon: "scanner" },
  { key: "signals", title: "Signals", icon: "bell" },
  { key: "trades", title: "My trades", icon: "trades" },
  { key: "alerts", title: "Price alerts", icon: "alarm" },
];

// Under the panels' icons, the signals page's own button (23/11/26); a panel icon clicked
// while the page is open goes back to the chart with that panel.
function SideRail({ side, onSide, alertCount, page, onPage }) {
  return (
    <nav className="tv-rail">
      {SIDE_PANELS.map((p) => (
        <button
          key={p.key}
          type="button"
          className={`tv-rail-btn${!page && side === p.key ? " active" : ""}`}
          onClick={() => {
            onPage(false);
            onSide(!page && side === p.key ? null : p.key);
          }}
          title={p.title}
        >
          {ICONS[p.icon]}
          {p.key === "alerts" && alertCount > 0 && <span className="tv-rail-badge">{alertCount}</span>}
        </button>
      ))}
      <span className="tv-rail-sep" />
      <button type="button" className={`tv-rail-btn${page ? " active" : ""}`} onClick={() => onPage(!page)} title="All signals page">
        {ICONS.gallery}
      </button>
    </nav>
  );
}

// The watchlist panel; a click on a row switches the chart.
function WatchlistPanel({ open, watchlist, quotes, symbol, onPick, onAdd, onRemove }) {
  if (!open) return null;
  return (
    <aside className="tv-side">
      {/* No title or column names, only the + and the rows (14/11/26). */}
      <div className="tv-watch-head">
        <button type="button" className="tv-icon-btn" onClick={onAdd} title="Add symbol">
          {ICONS.plus}
        </button>
      </div>
      <div className="tv-watch-rows">
        {watchlist.map((s) => {
          const q = quotes[s];
          const pct = q?.change_percent;
          const digits = q?.price != null ? precisionFor(s, q.price) : 2;
          const tone = pct == null ? "" : pct >= 0 ? " up" : " down";
          return (
            <div
              key={s}
              role="button"
              tabIndex={-1}
              className={`tv-watch-row${s === symbol ? " active" : ""}`}
              onClick={() => onPick(s)}
              title={q?.name || s}
            >
              <b>{shownSymbol(s)}</b>
              <span>{q?.price != null ? q.price.toFixed(digits) : "—"}</span>
              <span className={tone}>{pct != null ? `${signed(pct, 2)}%` : ""}</span>
              <button
                type="button"
                className="tv-watch-remove"
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(s);
                }}
                title="Remove from watchlist"
              >
                {ICONS.close}
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

// The bots' alerts that went out (the app's signals page, minus its history examples), by
// strategy, newest first; a click on one opens its chart (the user's ask, 13/11/26).
function SignalsPanel({ open, symbol, onPick }) {
  const [kind, setKind] = useState("weekly");
  const [lists, setLists] = useState({}); // kind → signals, or "error"

  useEffect(() => {
    if (!open) return;
    let stale = false;
    setLists({});
    SIGNAL_KINDS.forEach(({ value }) =>
      getSignals(value)
        .then((all) => !stale && setLists((l) => ({ ...l, [value]: all.filter((s) => !s.example) })))
        .catch(() => !stale && setLists((l) => ({ ...l, [value]: "error" })))
    );
    return () => {
      stale = true;
    };
  }, [open]);

  if (!open) return null;
  const list = lists[kind];

  return (
    <aside className="tv-side">
      <div className="tv-watch-head">
        <span>Signals</span>
      </div>
      <div className="tv-sig-tabs">
        {SIGNAL_KINDS.map((k) => (
          <button key={k.value} type="button" className={`tv-btn${kind === k.value ? " active" : ""}`} onClick={() => setKind(k.value)}>
            {k.label}
            {Array.isArray(lists[k.value]) && <span className="tv-sig-count">{lists[k.value].length}</span>}
          </button>
        ))}
      </div>
      <div className="tv-watch-rows">
        {list === undefined && <p className="tv-modal-empty">Loading…</p>}
        {list === "error" && <p className="tv-modal-empty tv-error">Server unavailable</p>}
        {Array.isArray(list) && !list.length && <p className="tv-modal-empty">No signals yet</p>}
        {Array.isArray(list) &&
          list.map((s) => (
            <div
              key={s.id}
              role="button"
              tabIndex={-1}
              className={`tv-watch-row tv-sig-row${s.symbol === symbol ? " active" : ""}`}
              onClick={() => onPick(s.symbol)}
              title={s.name || s.symbol}
            >
              <b>{shownSymbol(s.symbol)}</b>
              <span className={s.direction || ""}>{s.direction === "up" ? "▲ Up" : s.direction === "down" ? "▼ Down" : ""}</span>
              <span>{signalTime(s.created_at)}</span>
            </div>
          ))}
      </div>
    </aside>
  );
}

// The signals page (the user's ask, 23/11/26: "a special page in the panel to see all the
// signals"): every alert the bots sent, all strategies or one, as cards with the chart picture
// it went out with, covering the chart. This week by day on top, past weeks below, a row each,
// opened with a click (so their pictures load only then). A click on a picture opens it full
// size, a click on the symbol opens its chart in the panel.
const PAGE_KINDS = [{ value: "all", label: "All" }, ...SIGNAL_KINDS];
const KIND_LABELS = Object.fromEntries(SIGNAL_KINDS.map((k) => [k.value, k.label]));
const PAGE_REFRESH_MS = 60_000;

// The Sunday the date's week starts on, at midnight local time (as on the app's page).
function weekStart(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - d.getDay());
  return d;
}

// [key, label, items] in the list's order, from keyOf/labelOf of each group's first item.
function groupBy(list, keyOf, labelOf) {
  const groups = [];
  for (const item of list) {
    const key = keyOf(item);
    if (groups.at(-1)?.[0] !== key) groups.push([key, labelOf(item), []]);
    groups.at(-1)[2].push(item);
  }
  return groups;
}

function dayTitle(iso) {
  return new Date(iso).toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long" });
}

function shortDay(date) {
  return date.toLocaleDateString(LOCALE, { day: "numeric", month: "short" });
}

function clockTime(iso) {
  return new Date(iso).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

function SignalCard({ signal, showKind, onPick, onPicture }) {
  return (
    <article className="tv-card">
      <div className="tv-card-head">
        <button
          type="button"
          className="tv-card-symbol"
          onClick={() => onPick(signal.symbol)}
          title={`Open ${signal.name || signal.symbol} on the chart`}
        >
          {shownSymbol(signal.symbol)}
        </button>
        {signal.direction && (
          <span className={`tv-card-dir ${signal.direction}`}>{signal.direction === "up" ? "▲ Up" : "▼ Down"}</span>
        )}
        {showKind && <span className="tv-card-tag">{KIND_LABELS[signal.kind] || signal.kind}</span>}
        {signal.strong && <span className="tv-card-tag">Strong</span>}
        <span className="tv-card-time">{clockTime(signal.created_at)}</span>
      </div>
      {signal.has_picture ? (
        <button type="button" className="tv-card-pic" onClick={() => onPicture(signal)} title="Full size">
          <img src={signalPictureUrl(signal.id)} alt={signal.symbol} loading="lazy" />
        </button>
      ) : (
        <p className="tv-card-none">No picture</p>
      )}
      {signal.details && (
        <p className="tv-card-details" dir="auto">
          {signal.details}
        </p>
      )}
    </article>
  );
}

function SignalDays({ signals, showKind, onPick, onPicture }) {
  return groupBy(
    signals,
    (s) => new Date(s.created_at).toDateString(),
    (s) => dayTitle(s.created_at)
  ).map(([key, title, list]) => (
    <section key={key} className="tv-page-day">
      <h3>
        {title} <span>· {list.length}</span>
      </h3>
      <div className="tv-cards">
        {list.map((s) => (
          <SignalCard key={s.id} signal={s} showKind={showKind} onPick={onPick} onPicture={onPicture} />
        ))}
      </div>
    </section>
  ));
}

function SignalsPage({ open, onPick, onClose }) {
  const [kind, setKind] = useState("all");
  const [lists, setLists] = useState({}); // kind → signals, or "error"
  const [openWeeks, setOpenWeeks] = useState(() => new Set());
  const [picture, setPicture] = useState(null);

  useEffect(() => {
    if (!open) return;
    let stale = false;
    const load = () =>
      SIGNAL_KINDS.forEach(({ value }) =>
        getSignals(value)
          .then((all) => !stale && setLists((l) => ({ ...l, [value]: all.filter((s) => !s.example) })))
          // A failed refresh keeps the list already shown.
          .catch(() => !stale && setLists((l) => (Array.isArray(l[value]) ? l : { ...l, [value]: "error" })))
      );
    load();
    const id = setInterval(() => document.visibilityState === "visible" && load(), PAGE_REFRESH_MS);
    return () => {
      stale = true;
      clearInterval(id);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e) => {
      if (e.key !== "Escape") return;
      if (picture) setPicture(null);
      else onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, picture, onClose]);

  if (!open) return null;

  const loaded = SIGNAL_KINDS.map(({ value }) => lists[value]);
  let list; // undefined loading | "error" | signals, newest first
  if (kind !== "all") list = lists[kind];
  else if (loaded.some((l) => l === undefined)) list = undefined;
  else if (loaded.every((l) => l === "error")) list = "error";
  else list = loaded.filter(Array.isArray).flat().sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const countOf = (value) => {
    if (value === "all") return loaded.some(Array.isArray) ? loaded.filter(Array.isArray).reduce((n, l) => n + l.length, 0) : null;
    return Array.isArray(lists[value]) ? lists[value].length : null;
  };

  const thisWeek = weekStart(new Date()).getTime();
  const weeks = Array.isArray(list)
    ? groupBy(
        list,
        (s) => weekStart(new Date(s.created_at)).getTime(),
        (s) => weekStart(new Date(s.created_at))
      )
    : [];
  const current = weeks.find(([key]) => key === thisWeek)?.[2] ?? [];
  const past = weeks.filter(([key]) => key < thisWeek);
  const showKind = kind === "all";
  const toggleWeek = (key) =>
    setOpenWeeks((set) => {
      const next = new Set(set);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  return (
    <section className="tv-page">
      <header className="tv-page-head">
        <h2>Signals</h2>
        <div className="tv-page-tabs">
          {PAGE_KINDS.map((k) => (
            <button key={k.value} type="button" className={`tv-btn${kind === k.value ? " active" : ""}`} onClick={() => setKind(k.value)}>
              {k.label}
              {countOf(k.value) != null && <span className="tv-sig-count">{countOf(k.value)}</span>}
            </button>
          ))}
        </div>
        <button type="button" className="tv-icon-btn tv-page-close" onClick={onClose} title="Back to the chart">
          {ICONS.close}
        </button>
      </header>

      <div className="tv-page-body">
        {list === undefined && <p className="tv-modal-empty">Loading…</p>}
        {list === "error" && <p className="tv-modal-empty tv-error">Server unavailable</p>}
        {Array.isArray(list) && !list.length && <p className="tv-modal-empty">No signals yet</p>}
        {Array.isArray(list) && list.length > 0 && (
          <>
            <h2 className="tv-page-section">This week</h2>
            {current.length ? (
              <SignalDays signals={current} showKind={showKind} onPick={onPick} onPicture={setPicture} />
            ) : (
              <p className="tv-modal-empty">No signals this week yet</p>
            )}
            {past.length > 0 && <h2 className="tv-page-section">History</h2>}
            {past.map(([key, start, signals]) => {
              const end = new Date(start);
              end.setDate(end.getDate() + 6); // by the calendar: a clock change makes one day 23 or 25 hours
              const isOpen = openWeeks.has(key);
              return (
                <section key={key} className="tv-page-week">
                  <button type="button" className={`tv-page-week-head${isOpen ? " open" : ""}`} onClick={() => toggleWeek(key)}>
                    <span>
                      Week {shortDay(start)} – {shortDay(end)}
                    </span>
                    <span className="tv-sig-count">
                      {signals.length} signals {isOpen ? "▴" : "▾"}
                    </span>
                  </button>
                  {isOpen && <SignalDays signals={signals} showKind={showKind} onPick={onPick} onPicture={setPicture} />}
                </section>
              );
            })}
          </>
        )}
      </div>

      {picture && (
        <div className="tv-modal-shade tv-picture" onClick={() => setPicture(null)}>
          <img src={signalPictureUrl(picture.id)} alt={picture.symbol} />
        </div>
      )}
    </section>
  );
}

// The user's own trades (the user's ask, 19/11/26: their zones as a real chart they can zoom
// into, not a picture): a click opens the trade's chart with its zone; the timeframe buttons
// switch between daily, 4h, 1h and 15m.
function TradesPanel({ open, tradeId, onPick }) {
  const [trades, setTrades] = useState(null); // null loading | list | "error"

  useEffect(() => {
    if (!open) return;
    let stale = false;
    getMyTrades()
      // Newest first: the live site serves the file as it is, oldest first.
      .then((list) => !stale && setTrades([...list].sort((a, b) => b.id - a.id)))
      .catch(() => !stale && setTrades("error"));
    return () => {
      stale = true;
    };
  }, [open]);

  if (!open) return null;

  return (
    <aside className="tv-side">
      <div className="tv-watch-head">
        <span>My trades</span>
      </div>
      <div className="tv-watch-rows">
        {trades === null && <p className="tv-modal-empty">Loading…</p>}
        {trades === "error" && <p className="tv-modal-empty tv-error">Server unavailable</p>}
        {Array.isArray(trades) && !trades.length && <p className="tv-modal-empty">No trades yet</p>}
        {Array.isArray(trades) &&
          trades.map((t) => (
            <div
              key={t.id}
              role="button"
              tabIndex={-1}
              className={`tv-watch-row tv-trade-row${t.id === tradeId ? " active" : ""}`}
              onClick={() => onPick(t)}
              title={`Zone from ${signalTime(t.start)}`}
            >
              <b>{t.name}</b>
              <span>{tradeZoneText(t)}</span>
              <span>{signalTime(t.start)}</span>
            </div>
          ))}
      </div>
    </aside>
  );
}

// The stock scanner (the user's ask, 14/11/26): a tab per scan, its last result when first
// opened, Scan to run it again; a click on a row opens its chart. Each scan's result stays
// while the panel is closed, so a slow one that finishes meanwhile is there on the way back.
function ScannerPanel({ open, symbol, onPick }) {
  const [scanId, setScanId] = useState(SCANS[0].id);
  const [scans, setScans] = useState({}); // id → { view, loading, scanning, error }
  const startedRef = useRef(new Set());

  const current = SCANS.find((s) => s.id === scanId);
  const state = scans[current.id] ?? { loading: true };
  const rows = state.view?.rows ?? [];

  const patch = (id, next) => setScans((all) => ({ ...all, [id]: { ...all[id], ...next } }));

  const scan = (s) => {
    patch(s.id, { scanning: true, error: null });
    s.run()
      .then((data) => patch(s.id, { view: s.toView(data), scanning: false }))
      .catch((err) => patch(s.id, { error: scanError(err), scanning: false }));
  };

  // A scan's first time open: its last cached result, or (autoRun) a new scan when there's none.
  useEffect(() => {
    if (!open || startedRef.current.has(current.id)) return;
    startedRef.current.add(current.id);
    patch(current.id, { loading: true });
    current
      .latest()
      .then((data) => patch(current.id, { view: current.toView(data), loading: false }))
      .catch((err) => {
        patch(current.id, { loading: false, error: current.autoRun && !(err instanceof TypeError) ? null : scanError(err) });
        if (current.autoRun && !(err instanceof TypeError)) scan(current);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.id, open]);

  if (!open) return null;

  return (
    <aside className="tv-side">
      <div className="tv-watch-head">
        <span>Scanner</span>
        <button type="button" className="tv-alert-create" onClick={() => scan(current)} disabled={state.scanning}>
          {state.scanning ? "Scanning…" : "Scan"}
        </button>
      </div>
      <div className="tv-sig-tabs tv-scan-tabs">
        {SCANS.map((s) => (
          <button key={s.id} type="button" className={`tv-btn${s.id === current.id ? " active" : ""}`} onClick={() => setScanId(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      {state.view?.scannedAt && (
        <p className="tv-scan-note">
          Last scan {signalTime(state.view.scannedAt)} · {rows.length} symbols
        </p>
      )}
      <div className="tv-watch-rows">
        {state.loading && <p className="tv-modal-empty">Loading…</p>}
        {!state.loading && state.scanning && !state.view && (
          <p className="tv-modal-empty">Scanning… the first scan can take a minute or two</p>
        )}
        {state.error && <p className="tv-modal-empty tv-error">{state.error}</p>}
        {!state.loading && !state.error && state.view && !rows.length && <p className="tv-modal-empty">No results right now</p>}
        {rows.map((r) => (
          <div
            key={r.symbol}
            role="button"
            tabIndex={-1}
            className={`tv-watch-row tv-scan-row${r.symbol === symbol ? " active" : ""}`}
            onClick={() => onPick(r.symbol)}
          >
            <b>{shownSymbol(r.symbol)}</b>
            <span className={r.tone}>{r.badge}</span>
            <span>{r.price != null ? Number(r.price).toFixed(precisionFor(r.symbol, r.price)) : "—"}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}

// Price alerts (the user's ask, 13/11/26): a price for the symbol on the chart, and once it's
// reached a notification goes to the phone (backend price_alerts.py), then it's gone from
// here. Every alert still waiting is listed; a click on one opens its chart. A new one shows
// in the list under the form (the panel stays open, as TradingView's alerts panel does).
function AlertsPanel({ open, symbol, lastPrice, digits, alerts, onCreate, onDelete, onPick }) {
  const [price, setPrice] = useState("");
  const [error, setError] = useState(null);

  // The last price to start from, each time it opens or the symbol changes.
  useEffect(() => {
    if (open) setPrice(lastPrice != null ? lastPrice.toFixed(digits) : "");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, symbol]);

  if (!open) return null;

  const create = () => {
    const value = Number(price);
    if (!(value > 0)) return setError("Enter a price");
    setError(null);
    onCreate(symbol, value).catch((err) => setError(err instanceof TypeError ? "Server unavailable" : err.message));
  };

  return (
    <aside className="tv-side">
      <div className="tv-watch-head">
        <span>Price alerts</span>
      </div>
      <form
        className="tv-alert-form"
        onSubmit={(e) => {
          e.preventDefault();
          create();
        }}
      >
        <b>{shownSymbol(symbol)}</b>
        <input type="number" step="any" min="0" value={price} onChange={(e) => setPrice(e.target.value)} />
        <button type="submit" className="tv-alert-create">
          Create
        </button>
      </form>
      {error && <p className="tv-modal-empty tv-error">{error}</p>}
      <div className="tv-watch-rows">
        {!alerts.length && <p className="tv-modal-empty">No alerts — the phone gets one when the price gets there</p>}
        {alerts.map((a) => (
          <div
            key={a.id}
            role="button"
            tabIndex={-1}
            className={`tv-watch-row tv-alert-row${a.symbol === symbol ? " active" : ""}`}
            onClick={() => onPick(a.symbol)}
          >
            <b>{shownSymbol(a.symbol)}</b>
            <span className={a.above ? "up" : "down"}>{a.above ? "▲" : "▼"}</span>
            <span>{a.price}</span>
            <button
              type="button"
              className="tv-watch-remove"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(a.id);
              }}
              title="Delete alert"
            >
              {ICONS.close}
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}

// A right-click on the chart (the user's ask, 14/11/26): as on TradingView, an alert at the
// price under the mouse.
function ChartMenu({ menu, symbol, digits, onCreate, onClose }) {
  const boxRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onPointerDown = (e) => !boxRef.current?.contains(e.target) && onClose();
    const onKeyDown = (e) => e.key === "Escape" && onClose();
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("wheel", onClose);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("wheel", onClose);
    };
  }, [onClose]);

  const add = () => {
    setError(null);
    onCreate(symbol, menu.price).then(onClose, (err) => setError(err instanceof TypeError ? "Server unavailable" : err.message));
  };

  // Kept inside the chart when the click is near its right or bottom edge.
  const left = Math.max(Math.min(menu.x, menu.width - 300), 0);
  const top = Math.max(Math.min(menu.y, menu.height - 60), 0);

  return (
    <div ref={boxRef} className="tv-ind-menu tv-ctx-menu" style={{ left, top }} onContextMenu={(e) => e.preventDefault()}>
      <button type="button" className="tv-ind-row" onClick={add}>
        {ICONS.alarm}
        <span>
          Add alert on {shownSymbol(symbol)} at {menu.price.toFixed(digits)}
        </span>
      </button>
      {error && <p className="tv-modal-empty tv-error">{error}</p>}
    </div>
  );
}

export default function TvApp() {
  const prefs = useMemo(readPrefs, []);
  const [watchlist, setWatchlist] = useState(() => (Array.isArray(prefs.watchlist) ? prefs.watchlist : DEFAULT_WATCHLIST));
  const [symbol, setSymbol] = useState(() => prefs.symbol || watchlist[0] || "AAPL");
  const [timeframe, setTimeframe] = useState(() =>
    TIMEFRAMES.some((tf) => tf.value === prefs.timeframe) ? prefs.timeframe : "1h"
  );
  const [showZones, setShowZones] = useState(prefs.showZones !== false);
  const [showBreaks, setShowBreaks] = useState(prefs.showBreaks !== false);
  const [showEma, setShowEma] = useState(prefs.showEma !== false);
  const [showReturns, setShowReturns] = useState(prefs.showReturns !== false);
  const [candleEdge, setCandleEdge] = useState(() => clamp(Number(prefs.candleEdge) || CANDLE_EDGE.initial, CANDLE_EDGE.min, CANDLE_EDGE.max));
  const [candleBody, setCandleBody] = useState(() => clamp(Number(prefs.candleBody) || CANDLE_BODY.initial, CANDLE_BODY.min, CANDLE_BODY.max));
  // The right side panel open (SIDE_PANELS key) or null; first time, the watchlist on a
  // computer and nothing on a phone, where it would cover the chart.
  const [side, setSide] = useState(() =>
    prefs.side === null || SIDE_PANELS.some((p) => p.key === prefs.side)
      ? prefs.side
      : window.innerWidth > 700
        ? "watchlist"
        : null
  );
  // The signals page over the chart (not saved, so the panel always opens on the chart).
  const [signalsPage, setSignalsPage] = useState(false);
  // Always the signals' light look since the moon button went (13/11/26), even for whoever
  // had dark saved.
  const theme = "light";
  const [candles, setCandles] = useState(null); // { symbol, timeframe, list }
  const [loadState, setLoadState] = useState(null); // null | "loading" | error message
  const [quotes, setQuotes] = useState({});
  const [search, setSearch] = useState(null); // null | { mode: "open" | "add", text }
  // The first and last candle on screen ({ first, last }).
  const [viewBars, setViewBars] = useState(null);
  const [priceAlerts, setPriceAlerts] = useState([]); // waiting, from the backend
  const [chartMenu, setChartMenu] = useState(null); // the right-click menu: { x, y, width, height, price }
  const [trade, setTrade] = useState(null); // the user's trade on the chart (the Trades box)
  const [live, setLive] = useState(null); // the latest price, every few seconds: { symbol, price }

  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const zonesRef = useRef(null);
  const paneFillRef = useRef(null);
  const emaRef = useRef(null);
  const returnsRef = useRef(null);
  const alertLinesRef = useRef(null);
  const tradeZoneRef = useRef(null);
  const livePriceRef = useRef(null);
  const framedRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({ symbol, timeframe, showZones, showBreaks, showEma, showReturns, candleEdge, candleBody, watchlist, side })
      );
    } catch {
      /* private mode etc. — prefs just don't stick */
    }
  }, [symbol, timeframe, showZones, showBreaks, showEma, showReturns, candleEdge, candleBody, watchlist, side]);

  // Before paint, so the page's colors switch together with the chart's.
  useLayoutEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { fontFamily: FONT, fontSize: 12 },
      crosshair: { mode: 0 },
      timeScale: { rightOffset: RIGHT_OFFSET, tickMarkFormatter },
      localization: { locale: LOCALE },
      // A flick keeps the chart gliding a little, as on TradingView (23/11/26).
      kineticScroll: { mouse: true, touch: true },
    });
    // Thick candle outlines and wicks (the built-in candles' are 1px); colored by the theme, below.
    // The library's own last-price line is off: LivePriceLine draws it, with the price beside it.
    const series = chart.addCustomSeries(new ThickCandlestickSeries(), {
      borderWidth: 3,
      wickWidth: 3,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    // First, so it's under the zones.
    const paneFill = new PaneFill();
    series.attachPrimitive(paneFill);
    const tradeZone = new TradeZone();
    series.attachPrimitive(tradeZone);
    // Matte blue zones: a muted, greyish blue that sits quietly on the grey background.
    // They end at the current candle, not the chart's right edge (15/11/26).
    const zones = new ZonesPrimitive(
      { zoneFill: "rgba(70, 100, 145, 0.35)", zoneLine: "rgba(55, 85, 130, 0.85)" },
      { toLastCandle: true }
    );
    series.attachPrimitive(zones);
    const returns = new ReturnLevelsPrimitive();
    series.attachPrimitive(returns);
    const alertLines = new AlertLines();
    series.attachPrimitive(alertLines);
    const livePrice = new LivePriceLine();
    series.attachPrimitive(livePrice);
    // Colored by the theme, below.
    const ema = chart.addSeries(LineSeries, {
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    // The canvas draws with a fallback font until Inter arrives from Google Fonts; redraw then.
    document.fonts?.load('600 12px "Inter"').then(() => chartRef.current?.applyOptions({ layout: { fontFamily: FONT } }));
    chartRef.current = chart;
    seriesRef.current = series;
    zonesRef.current = zones;
    paneFillRef.current = paneFill;
    emaRef.current = ema;
    returnsRef.current = returns;
    alertLinesRef.current = alertLines;
    tradeZoneRef.current = tradeZone;
    livePriceRef.current = livePrice;
    return () => {
      chartRef.current = null;
      seriesRef.current = null;
      zonesRef.current = null;
      paneFillRef.current = null;
      emaRef.current = null;
      returnsRef.current = null;
      alertLinesRef.current = null;
      tradeZoneRef.current = null;
      livePriceRef.current = null;
      chart.remove();
    };
  }, []);

  // TradingView's free drag (23/11/26): dragging the chart up or down moves the prices too,
  // not only the time, and the price stops fitting the candles by itself; a double click
  // brings that back (TradingView's "auto"), as does a new symbol or timeframe. The library
  // moves the prices itself once auto is off, so this only does the drag that turns it off.
  // Mouse only: on a phone two fingers zoom, and one of them would also move the prices.
  useEffect(() => {
    const el = containerRef.current;
    let drag = null;
    const onDown = (e) => {
      const chart = chartRef.current;
      if (e.button !== 0 || e.pointerType === "touch" || !chart) return;
      if (!chart.priceScale("right").options().autoScale) return;
      const paneHeight = chart.paneSize().height;
      const range = chart.priceScale("right").getVisibleRange();
      // Not on the time scale under the candles, which squeezes and stretches the time, nor on
      // the price scale, which squeezes and stretches the prices (the library does both).
      const box = el.getBoundingClientRect();
      if (!range || e.clientY - box.top > paneHeight || e.clientX - box.left > chart.paneSize().width) return;
      drag = { y: e.clientY, range, paneHeight, moved: false };
    };
    const onMove = (e) => {
      const chart = chartRef.current;
      if (!drag || !chart) return;
      const dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dy) < 4) return;
      drag.moved = true;
      const shift = (dy / drag.paneHeight) * (drag.range.to - drag.range.from);
      const scale = chart.priceScale("right");
      scale.setAutoScale(false);
      scale.setVisibleRange({ from: drag.range.from + shift, to: drag.range.to + shift });
    };
    const onUp = () => {
      drag = null;
    };
    const onDoubleClick = () => chartRef.current?.priceScale("right").setAutoScale(true);
    el.addEventListener("pointerdown", onDown, true);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    el.addEventListener("dblclick", onDoubleClick);
    return () => {
      el.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      el.removeEventListener("dblclick", onDoubleClick);
    };
  }, []);

  useEffect(() => {
    const c = CHART_THEMES[theme];
    const line = { color: c.crosshair, labelBackgroundColor: c.label };
    chartRef.current?.applyOptions({
      layout: { background: { color: c.axisBg }, textColor: c.text },
      // No grid and no line along the price and time scales — a bare chart (13/11/26).
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      crosshair: { vertLine: line, horzLine: line },
      // The price scale on the right, hidden on 15/11/26, is back since 24/11/26 for the live
      // price and its clock (LivePriceLine); dragging it up or down squeezes the chart.
      rightPriceScale: { visible: true, borderVisible: false },
      timeScale: { borderVisible: false },
    });
    paneFillRef.current?.setColor(c.bg);
    emaRef.current?.applyOptions({ color: c.ema });
    seriesRef.current?.applyOptions({
      upColor: c.up,
      downColor: c.down,
      borderColor: c.edge || "", // empty: each candle's own up/down color
      wickColor: c.edge || "",
    });
  }, [theme]);

  useEffect(() => {
    seriesRef.current?.applyOptions({ borderWidth: candleEdge, wickWidth: candleEdge, barWidthFactor: candleBody / 100 });
  }, [candleEdge, candleBody]);

  // Loads the symbol on screen when it or the timeframe changes, then refreshes it every
  // minute; a newer load wins.
  useEffect(() => {
    if (!symbol) return;
    let stale = false;
    let closeTimer = null;
    const load = (first) => {
      if (first) setLoadState("loading");
      getCandles(symbol, timeframe)
        .then((data) => {
          if (stale) return;
          if (!data.candles.length) throw new Error(`No candles for ${symbol}`);
          const list = data.candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
          setCandles({ symbol, timeframe, list });
          setLoadState(null);
          // Again just after the last candle closes (if within the minute), for the next one
          // and its clock.
          clearTimeout(closeTimer);
          const left = candleCloseAt(symbol, timeframe, list[list.length - 1].time) * 1000 - Date.now();
          if (left > 0 && left < CANDLES_EVERY_MS) closeTimer = setTimeout(() => load(false), left + 3000);
        })
        .catch((err) => {
          if (stale || !first) return;
          setLoadState(err instanceof TypeError ? "Server unavailable — the backend must be running (port 8001)" : err.message);
        });
    };
    load(true);
    const id = setInterval(() => load(false), CANDLES_EVERY_MS);
    return () => {
      stale = true;
      clearInterval(id);
      clearTimeout(closeTimer);
    };
  }, [symbol, timeframe]);

  // The live price of the symbol on screen, every LIVE_EVERY_MS while the page is in view;
  // only the newest answer counts.
  useEffect(() => {
    if (!symbol) return;
    let stale = false;
    let sent = 0;
    let shown = 0;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      const n = ++sent;
      getLivePrice(symbol)
        .then(({ price }) => {
          if (stale || n < shown || !(price > 0)) return;
          shown = n;
          setLive((prev) => (prev?.symbol === symbol && prev.price === price ? prev : { symbol, price }));
          // Its watchlist row moves with it, today's change kept from the same last close.
          setQuotes((all) => {
            const q = all[symbol];
            if (q?.price == null || q.price === price) return all;
            const prevClose = q.change_percent != null ? q.price / (1 + q.change_percent / 100) : null;
            const change_percent = prevClose ? ((price - prevClose) / prevClose) * 100 : q.change_percent;
            return { ...all, [symbol]: { ...q, price, change_percent } };
          });
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, LIVE_EVERY_MS);
    return () => {
      stale = true;
      clearInterval(id);
    };
  }, [symbol]);

  // The watchlist's prices, refreshed every minute; plus the name of a symbol on the chart
  // that isn't in it, for the legend.
  useEffect(() => {
    let stale = false;
    const load = () =>
      watchlist.forEach((s) =>
        getQuote(s)
          .then((q) => !stale && setQuotes((all) => ({ ...all, [s]: q })))
          .catch(() => {})
      );
    load();
    const id = setInterval(load, QUOTES_EVERY_MS);
    return () => {
      stale = true;
      clearInterval(id);
    };
  }, [watchlist]);

  useEffect(() => {
    if (!symbol || watchlist.includes(symbol)) return;
    let stale = false;
    getQuote(symbol)
      .then((q) => !stale && setQuotes((all) => ({ ...all, [symbol]: q })))
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [symbol, watchlist]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || !candles) return;
    const { list } = candles;
    const precision = precisionFor(candles.symbol, list[list.length - 1].close);
    series.applyOptions({ priceFormat: { type: "price", precision, minMove: 10 ** -precision } });
    const intraday = !["1d", "1w", "1mo"].includes(candles.timeframe);
    chart.applyOptions({
      timeScale: { timeVisible: intraday, secondsVisible: false },
      localization: { timeFormatter: intraday ? crosshairTime : crosshairDate },
    });
    // Each candle's own color tints the last price's line and label, as the built-in candles did.
    const c = CHART_THEMES[theme];
    series.setData(list.map((b) => ({ ...b, color: b.close >= b.open ? c.up : c.down })));
  }, [candles, theme]);

  // EMA 50 of all the loaded candles, like TradingView's (not only the ones on screen).
  const emaLine = useMemo(() => (candles ? computeEma(candles.list, EMA_PERIOD) : []), [candles]);

  useEffect(() => {
    emaRef.current?.applyOptions({ visible: showEma });
    emaRef.current?.setData(showEma ? emaLine : []);
  }, [emaLine, showEma]);

  // The live price moves the last candle (and EMA 50's last point) between the minute loads,
  // and the price line with it; after a load it goes back on the new last candle.
  useEffect(() => {
    const series = seriesRef.current;
    const line = livePriceRef.current;
    if (!series || !line) return;
    const list = candles?.list;
    if (!list?.length) {
      line.set(null);
      return;
    }
    const c = CHART_THEMES[theme];
    let bar = list[list.length - 1];
    const price = live?.symbol === candles.symbol ? live.price : null;
    if (price != null) {
      bar = { ...bar, close: price, high: Math.max(bar.high, price), low: Math.min(bar.low, price) };
      series.update({ ...bar, color: bar.close >= bar.open ? c.up : c.down });
      if (showEma && emaLine.length > 1) {
        const alpha = 2 / (EMA_PERIOD + 1);
        emaRef.current?.update({ time: bar.time, value: price * alpha + emaLine[emaLine.length - 2].value * (1 - alpha) });
      }
    }
    line.set({
      price: bar.close,
      color: bar.close >= bar.open ? c.up : c.down,
      precision: precisionFor(candles.symbol, list[list.length - 1].close),
      closeAt: candleCloseAt(candles.symbol, candles.timeframe, bar.time),
      text: c.text,
      axisBg: c.axisBg,
    });
  }, [candles, live, theme, emaLine, showEma]);

  // The trade picked in the Trades box, while its symbol is the one loaded.
  const shownTrade = trade && candles?.symbol === trade.symbol ? trade : null;

  // A trade stays on the chart until another symbol is opened.
  useEffect(() => {
    if (trade && trade.symbol !== symbol) setTrade(null);
  }, [trade, symbol]);

  // A trade's link from Telegram: tv.html?trade=<id> opens it.
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("trade"));
    if (!id) return;
    getMyTrades()
      .then((list) => {
        const found = list.find((t) => t.id === id);
        if (!found) return;
        setSymbol(found.symbol);
        setTrade(found);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const list = candles?.list;
    tradeZoneRef.current?.set(
      shownTrade && list.length
        ? { bottom: shownTrade.bottom, top: shownTrade.top, fromIdx: tradeStartIdx(list, shownTrade), lastIdx: list.length - 1 }
        : null
    );
  }, [candles, shownTrade]);

  // Frames newly loaded candles on the latest VISIBLE_BARS, or with a trade open, from
  // TRADE_LEAD_BARS before its zone began. The minute refresh of the same chart keeps wherever
  // the user panned to.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !candles) return;
    const key = `${candles.symbol}|${candles.timeframe}|${shownTrade?.id ?? ""}`;
    if (framedRef.current === key) return;
    framedRef.current = key;
    const n = candles.list.length;
    const from = shownTrade ? tradeStartIdx(candles.list, shownTrade) - TRADE_LEAD_BARS : n - VISIBLE_BARS;
    chart.priceScale("right").applyOptions({ autoScale: true });
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(from, 0), to: n - 1 + RIGHT_OFFSET });
  }, [candles, shownTrade]);

  // Follows the candles on screen as the chart pans and zooms (blank space past either
  // end counts as the end candle). Only a change of candle re-runs the marking.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !candles) return;
    const timeScale = chart.timeScale();
    const lastIdx = candles.list.length - 1;
    const update = (range) => {
      const next = range && { first: clamp(Math.ceil(range.from), 0, lastIdx), last: clamp(Math.floor(range.to), 0, lastIdx) };
      setViewBars((prev) => (prev && next && prev.first === next.first && prev.last === next.last ? prev : next));
    };
    update(timeScale.getVisibleLogicalRange());
    timeScale.subscribeVisibleLogicalRangeChange(update);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(update);
  }, [candles]);

  // Like the desktop chart window (trainer.html), the marking is of the candles on screen
  // only, as if the last of them were today, plus the PIVOT_BARS before them so a touch on
  // the first candle on screen still counts. Both markings are of strong zones only (touched
  // STRONG_TOUCHES+ times), so every break-and-retest sits on a zone that's drawn.
  // With one of the user's trades open, only their zone (and EMA) is drawn: on 15m the bot's
  // zones and break-and-retest marks buried it (19/11/26).
  const marks = useMemo(() => {
    if (!candles || shownTrade || (!showZones && !showBreaks)) return EMPTY_MARKS;
    const { list } = candles;
    const last = clamp(viewBars?.last ?? list.length - 1, 0, list.length - 1);
    const first = Math.max(viewBars?.first ?? list.length - VISIBLE_BARS, 0);
    if (first > last) return EMPTY_MARKS;
    const found = detectZonesFrom(list.slice(0, last + 1), Math.max(first - PIVOT_BARS, 0), { minTouches: STRONG_TOUCHES });
    return { zones: showZones ? found.zones : [], events: showBreaks ? found.events : [] };
  }, [candles, viewBars, showZones, showBreaks, shownTrade]);

  useEffect(() => {
    zonesRef.current?.setState({ candles: candles?.list || [], zones: marks.zones, events: marks.events, texts: ZONE_TEXTS });
  }, [candles, marks]);

  // Return levels of the chart's own candles, whatever timeframe is on (the user's ask,
  // 15/11/26, in place of always 4h). Like the other markings, the moves that start on screen,
  // as if the last candle on it were today: a level the price came back to only after it still
  // shows as waiting.
  const returnLevels = useMemo(() => {
    if (!showReturns || shownTrade || !candles?.list.length) return [];
    const { list } = candles;
    const first = Math.max(viewBars?.first ?? list.length - VISIBLE_BARS, 0);
    const last = clamp(viewBars?.last ?? list.length - 1, 0, list.length - 1);
    const from = list[first].time;
    const indexOf = new Map(list.map((c, i) => [c.time, i]));
    return findReturnLevels(list.slice(0, last + 1))
      .filter((l) => l.fromTime >= from)
      .map((l) => ({
        dir: l.dir,
        price: l.price,
        edge: l.edge,
        fromIdx: indexOf.get(l.fromTime),
        endIdx: indexOf.get(l.endTime),
        returnIdx: l.returnTime == null ? null : indexOf.get(l.returnTime),
      }))
      .filter((l) => (l.returnIdx ?? last) - l.endIdx <= MAX_RETURN_BARS);
  }, [showReturns, shownTrade, candles, viewBars]);

  useEffect(() => {
    const list = candles?.list;
    returnsRef.current?.setLevels(
      returnLevels,
      list?.length ? precisionFor(candles.symbol, list[list.length - 1].close) : 2,
      list?.length ? list.length - 1 : 0
    );
  }, [returnLevels, candles]);

  // The price alerts still waiting, every minute: one that was reached is gone from the list.
  const loadPriceAlerts = () => getPriceAlerts().then(setPriceAlerts).catch(() => {});

  useEffect(() => {
    loadPriceAlerts();
    const id = setInterval(loadPriceAlerts, QUOTES_EVERY_MS);
    return () => clearInterval(id);
  }, []);

  const addPriceAlert = (s, price) =>
    createPriceAlert(s, price).then((a) => setPriceAlerts((list) => (list.some((b) => b.id === a.id) ? list : [...list, a])));

  const removePriceAlert = (id) => {
    setPriceAlerts((list) => list.filter((a) => a.id !== id));
    deletePriceAlert(id).catch(loadPriceAlerts);
  };

  // A dashed line at each alert of the symbol on the chart, as on TradingView, up to the
  // last candle (AlertLines); the price line only puts its price on the price scale.
  useEffect(() => {
    const list = candles?.symbol === symbol ? candles.list : [];
    alertLinesRef.current?.set(
      priceAlerts.filter((a) => a.symbol === symbol).map((a) => a.price),
      list.length ? list.length - 1 : null
    );
  }, [priceAlerts, symbol, candles]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const lines = priceAlerts
      .filter((a) => a.symbol === symbol)
      .map((a) =>
        series.createPriceLine({ price: a.price, color: ALERT_COLOR, lineVisible: false, axisLabelVisible: true, title: "" })
      );
    return () => {
      if (seriesRef.current === series) lines.forEach((line) => series.removePriceLine(line));
    };
  }, [priceAlerts, symbol]);

  const pickSymbol = (next) => {
    setSearch(null);
    setSymbol(next);
  };

  const addToWatchlist = (next) => {
    setSearch(null);
    setWatchlist((list) => (list.includes(next) ? list : [...list, next]));
  };

  const removeFromWatchlist = (gone) => setWatchlist((list) => list.filter((s) => s !== gone));

  // As on TradingView: typing on the chart opens the symbol search with that letter, and
  // ↑/↓ go through the watchlist.
  useEffect(() => {
    const onKeyDown = (e) => {
      if (search || signalsPage || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea")) return;
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        if (!watchlist.length) return;
        e.preventDefault();
        const at = watchlist.indexOf(symbol);
        const next = at === -1 ? 0 : clamp(at + (e.key === "ArrowDown" ? 1 : -1), 0, watchlist.length - 1);
        setSymbol(watchlist[next]);
      } else if (/^[a-z0-9^]$/i.test(e.key)) {
        e.preventDefault();
        setSearch({ mode: "open", text: e.key.toUpperCase() });
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [search, signalsPage, watchlist, symbol]);

  const hasCandles = !!candles?.list.length;
  const lastPrice =
    live?.symbol === symbol
      ? live.price
      : candles?.symbol === symbol && hasCandles
        ? candles.list[candles.list.length - 1].close
        : quotes[symbol]?.price;
  const priceDigits = lastPrice != null ? precisionFor(symbol, lastPrice) : 2;

  const openChartMenu = (e) => {
    e.preventDefault();
    const series = seriesRef.current;
    if (!series || candles?.symbol !== symbol || !hasCandles) return;
    const box = e.currentTarget.getBoundingClientRect();
    const y = e.clientY - box.top;
    const price = series.coordinateToPrice(y);
    if (!(price > 0)) return;
    setChartMenu({ x: e.clientX - box.left, y, width: box.width, height: box.height, price: Number(price.toFixed(priceDigits)) });
  };

  const closeChartMenu = useCallback(() => setChartMenu(null), []);
  const closeSignalsPage = useCallback(() => setSignalsPage(false), []);

  return (
    <div className={`tv${side ? " tv-side-open" : ""}`}>
      <header className="tv-top">
        <button type="button" className="tv-btn tv-symbol" onClick={() => setSearch({ mode: "open", text: "" })} title="Symbol search">
          <b>{shownSymbol(symbol)}</b>
        </button>
        <span className="tv-sep" />
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf.value}
            type="button"
            className={`tv-btn tv-interval${timeframe === tf.value ? " active" : ""}`}
            onClick={() => setTimeframe(tf.value)}
            title={tf.title}
          >
            {tf.label}
          </button>
        ))}
        <span className="tv-sep" />
        <IndicatorsMenu
          items={[
            {
              key: "zones",
              name: "Strong zones",
              title: `Zones the price touched ${STRONG_TOUCHES}+ times`,
              icon: ICONS.zones,
              on: showZones,
              toggle: () => setShowZones((on) => !on),
            },
            {
              key: "breaks",
              name: "Break & retest",
              title: "A zone broken, then retested",
              icon: ICONS.breaks,
              on: showBreaks,
              toggle: () => setShowBreaks((on) => !on),
            },
            {
              key: "ema",
              name: `EMA ${EMA_PERIOD}`,
              title: `Exponential moving average of the last ${EMA_PERIOD} closes`,
              icon: ICONS.ema,
              on: showEma,
              toggle: () => setShowEma((on) => !on),
            },
            {
              key: "returns",
              name: "Return levels",
              title: `Where a drop or rise (${MIN_RUN}+ candles in a row, breaking the last ${BREAK_LOOKBACK} candles' high or low) started and the price came back within ${MAX_RETURN_BARS} candles`,
              icon: ICONS.returns,
              on: showReturns,
              toggle: () => setShowReturns((on) => !on),
            },
          ]}
        />
        <CandleMenu edge={candleEdge} body={candleBody} onEdge={setCandleEdge} onBody={setCandleBody} />
        <span className="tv-sep" />
        {/* TradingView's Alert button: opens the alerts panel on the right. */}
        <button
          type="button"
          className={`tv-btn tv-btn-text${side === "alerts" ? " active" : ""}`}
          onClick={() => setSide((s) => (s === "alerts" ? null : "alerts"))}
          title="Price alerts"
        >
          {ICONS.alarm}
          Alert
        </button>
      </header>

      <main className="tv-chart-area">
        <div ref={containerRef} className="tv-chart" onContextMenu={openChartMenu} />
        {chartMenu && (
          <ChartMenu
            key={`${chartMenu.x},${chartMenu.y}`}
            menu={chartMenu}
            symbol={symbol}
            digits={priceDigits}
            onCreate={addPriceAlert}
            onClose={closeChartMenu}
          />
        )}
        {/* No legend: the indicators show only on the chart and in the Indicators menu (14/11/26). */}
        {loadState && (
          <div className="tv-overlay">
            {loadState === "loading" ? <p>Loading…</p> : <p className="tv-error">{loadState}</p>}
          </div>
        )}
      </main>

      {/* Mounted even while closed, so a scan in progress keeps its result. */}
      <WatchlistPanel
        open={side === "watchlist"}
        watchlist={watchlist}
        quotes={quotes}
        symbol={symbol}
        onPick={setSymbol}
        onAdd={() => setSearch({ mode: "add", text: "" })}
        onRemove={removeFromWatchlist}
      />
      <ScannerPanel open={side === "scanner"} symbol={symbol} onPick={setSymbol} />
      <SignalsPanel open={side === "signals"} symbol={symbol} onPick={setSymbol} />
      <TradesPanel
        open={side === "trades"}
        tradeId={trade?.id}
        onPick={(t) => {
          setSymbol(t.symbol);
          setTrade(t);
        }}
      />
      <AlertsPanel
        open={side === "alerts"}
        symbol={symbol}
        lastPrice={lastPrice}
        digits={priceDigits}
        alerts={priceAlerts}
        onCreate={addPriceAlert}
        onDelete={removePriceAlert}
        onPick={setSymbol}
      />
      <SignalsPage
        open={signalsPage}
        onPick={(s) => {
          setSymbol(s);
          setSignalsPage(false);
        }}
        onClose={closeSignalsPage}
      />
      <SideRail side={side} onSide={setSide} alertCount={priceAlerts.length} page={signalsPage} onPage={setSignalsPage} />

      {search && (
        <SymbolSearch
          initial={search.text}
          title={search.mode === "add" ? "Add to watchlist" : "Symbol search"}
          onPick={search.mode === "add" ? addToWatchlist : pickSymbol}
          onClose={() => setSearch(null)}
        />
      )}
    </div>
  );
}
