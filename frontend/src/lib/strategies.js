// Well-known published trading strategies run over a chart's candles. Each one turns the
// candles into trades, and backtest() adds trading costs and the summary numbers.
// Rules and parameters are the published defaults (nothing is tuned to the data):
// - Close signals trade at the next candle's open (no peeking at the close they came from).
// - Breakouts and stops trade intrabar at their level, or at the open if price gapped past it.
// - Every trade puts the whole account in, with no leverage.
// No imports, so the research script (node) can load this file as is.

const WEEK_SECONDS = 7 * 24 * 3600;
// The bar a strategy must clear to be offered: 0.5% a week, compounded, after costs.
export const TARGET_WEEKLY = 0.005;

function sma(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// pandas .ewm(span, adjust=False).mean(), the same as lib/ema.js.
function ema(values, period) {
  const alpha = 2 / (period + 1);
  const out = [];
  let prev = values[0];
  for (let i = 0; i < values.length; i++) {
    prev = i === 0 ? values[0] : values[i] * alpha + prev * (1 - alpha);
    out.push(prev);
  }
  return out;
}

// Wilder's smoothing, seeded with the plain average of the first `period` values.
function wilder(values, period, firstIdx) {
  const out = new Array(values.length).fill(null);
  if (values.length < firstIdx + period) return out;
  let avg = 0;
  for (let i = firstIdx; i < firstIdx + period; i++) avg += values[i];
  avg /= period;
  out[firstIdx + period - 1] = avg;
  for (let i = firstIdx + period; i < values.length; i++) {
    avg = (avg * (period - 1) + values[i]) / period;
    out[i] = avg;
  }
  return out;
}

function rsi(closes, period) {
  const gains = closes.map((c, i) => (i === 0 ? 0 : Math.max(0, c - closes[i - 1])));
  const losses = closes.map((c, i) => (i === 0 ? 0 : Math.max(0, closes[i - 1] - c)));
  const avgGain = wilder(gains, period, 1);
  const avgLoss = wilder(losses, period, 1);
  return closes.map((_, i) => {
    if (avgGain[i] == null) return null;
    if (avgLoss[i] === 0) return avgGain[i] === 0 ? 50 : 100;
    return 100 - 100 / (1 + avgGain[i] / avgLoss[i]);
  });
}

function atr(candles, period) {
  const tr = candles.map((c, i) =>
    i === 0
      ? c.high - c.low
      : Math.max(c.high - c.low, Math.abs(c.high - candles[i - 1].close), Math.abs(c.low - candles[i - 1].close))
  );
  return wilder(tr, period, 0);
}

// Population standard deviation over a rolling window, as Bollinger Bands use.
function rollingStd(values, period, means) {
  return values.map((_, i) => {
    if (means[i] == null) return null;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) sq += (values[j] - means[i]) ** 2;
    return Math.sqrt(sq / period);
  });
}

function highest(values, from, to) {
  let best = -Infinity;
  for (let i = from; i <= to; i++) best = Math.max(best, values[i]);
  return best;
}

function lowest(values, from, to) {
  let best = Infinity;
  for (let i = from; i <= to; i++) best = Math.min(best, values[i]);
  return best;
}

// target(i, side) is the position wanted after candle i closes (1 long, -1 short, 0 flat).
// A change trades at the next candle's open; one on the last candle is returned as
// `signal`, to be filled at the open of the candle that hasn't printed yet.
function runOnClose(candles, from, target) {
  const trades = [];
  let open = null;
  let signal = null;
  for (let i = from; i < candles.length; i++) {
    const side = open ? open.side : 0;
    const want = target(i, side);
    if (want === side) continue;
    if (i === candles.length - 1) {
      signal = { side: want, from: side };
      break;
    }
    const fill = candles[i + 1].open;
    if (open) trades.push({ ...open, exitIdx: i + 1, exitPrice: fill, reason: "signal" });
    open = want ? { side: want, entryIdx: i + 1, entryPrice: fill } : null;
  }
  if (open) trades.push({ ...open, exitIdx: null, exitPrice: null, reason: null });
  return { trades, signal };
}

// Donchian channel breakout with the Turtles' 2N stop (N = ATR 20): a stop order one tick
// past the highest high / lowest low of the last `entryLen` candles opens the trade; the
// opposite `exitLen` channel or the stop, whichever is nearer, closes it.
function runBreakout(candles, entryLen, exitLen) {
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const n = atr(candles, 20);
  const trades = [];
  let open = null;
  for (let i = Math.max(entryLen, 20); i < candles.length; i++) {
    const c = candles[i];
    if (open) {
      const channel = open.side === 1 ? lowest(lows, i - exitLen, i - 1) : highest(highs, i - exitLen, i - 1);
      const level = open.side === 1 ? Math.max(channel, open.stop) : Math.min(channel, open.stop);
      const hit = open.side === 1 ? c.low <= level : c.high >= level;
      if (hit) {
        const price = open.side === 1 ? Math.min(c.open, level) : Math.max(c.open, level);
        trades.push({ ...open, exitIdx: i, exitPrice: price, reason: level === open.stop ? "stop" : "signal" });
        open = null;
      }
      continue;
    }
    const up = highest(highs, i - entryLen, i - 1);
    const down = lowest(lows, i - entryLen, i - 1);
    const hitUp = c.high > up;
    const hitDown = c.low < down;
    if ((!hitUp && !hitDown) || n[i - 1] == null) continue;
    // Both sides in one candle: the level nearer the open was reached first.
    const side = hitUp && (!hitDown || up - c.open <= c.open - down) ? 1 : -1;
    const entryPrice = side === 1 ? Math.max(c.open, up) : Math.min(c.open, down);
    const stop = entryPrice - side * 2 * n[i - 1];
    open = { side, entryIdx: i, entryPrice, stop };
    // The same candle closing past the stop means the stop was hit after the entry.
    if (side === 1 ? c.close <= stop : c.close >= stop) {
      trades.push({ ...open, exitIdx: i, exitPrice: stop, reason: "stop" });
      open = null;
    }
  }
  if (open) trades.push({ ...open, exitIdx: null, exitPrice: null, reason: null });
  return { trades, signal: null };
}

function crossUp(a, b, i) {
  return a[i - 1] != null && b[i - 1] != null && a[i] > b[i] && a[i - 1] <= b[i - 1];
}

function crossDown(a, b, i) {
  return a[i - 1] != null && b[i - 1] != null && a[i] < b[i] && a[i - 1] >= b[i - 1];
}

export const STRATEGIES = [
  {
    id: "rsi2",
    short: "RSI(2)",
    name: "RSI(2) של קונורס",
    source: "Larry Connors, Short Term Trading Strategies That Work (2008)",
    rules: "קנייה: סגירה מעל ממוצע 200 ו-RSI(2) מתחת ל-10. יציאה: סגירה מעל ממוצע 5.",
    sides: "long",
    warmup: 200,
    run(candles) {
      const closes = candles.map((c) => c.close);
      const ma200 = sma(closes, 200);
      const ma5 = sma(closes, 5);
      const r = rsi(closes, 2);
      return runOnClose(candles, 199, (i, side) => {
        if (side === 0) return closes[i] > ma200[i] && r[i] < 10 ? 1 : 0;
        return closes[i] > ma5[i] ? 0 : side;
      });
    },
  },
  {
    id: "double7",
    short: "Double 7s",
    name: "Double 7s של קונורס",
    source: "Larry Connors & Cesar Alvarez, Short Term Trading Strategies That Work (2008)",
    rules: "קנייה: סגירה מעל ממוצע 200 והסגירה הנמוכה ביותר של 7 הנרות. יציאה: הסגירה הגבוהה ביותר של 7 הנרות.",
    sides: "long",
    warmup: 200,
    run(candles) {
      const closes = candles.map((c) => c.close);
      const ma200 = sma(closes, 200);
      return runOnClose(candles, 199, (i, side) => {
        if (side === 0) return closes[i] > ma200[i] && closes[i] <= lowest(closes, i - 6, i) ? 1 : 0;
        return closes[i] >= highest(closes, i - 6, i) ? 0 : side;
      });
    },
  },
  {
    id: "turtle2",
    short: "צבים 55/20",
    name: "צבים — מערכת 2",
    source: "Richard Dennis / Curtis Faith, Way of the Turtle (2007)",
    rules: "כניסה בפריצת השיא (או השפל) של 55 נרות, לונג או שורט. יציאה בפריצה הפוכה של 20 נרות או בסטופ של 2 ATR.",
    sides: "both",
    warmup: 55,
    run: (candles) => runBreakout(candles, 55, 20),
  },
  {
    id: "turtle1",
    short: "צבים 20/10",
    name: "צבים — מערכת 1 (דונצ'יאן 20/10)",
    source: "Richard Dennis / Curtis Faith, Way of the Turtle (2007) — בלי מסנן העסקה הקודמת",
    rules: "כניסה בפריצת השיא (או השפל) של 20 נרות, לונג או שורט. יציאה בפריצה הפוכה של 10 נרות או בסטופ של 2 ATR.",
    sides: "both",
    warmup: 20,
    run: (candles) => runBreakout(candles, 20, 10),
    verified: {
      market: "crypto",
      timeframe: "1d",
      where: "קריפטו על גרף יומי",
      text: "עברה את הרף (0.5% לשבוע) רק בקריפטו על גרף יומי: חציון 1.05% לשבוע על 16 מטבעות, על כל ההיסטוריה של כל מטבע (עד 10 שנים; 14 מתוך 16 מעל 0.5%). ב-3 השנים האחרונות החציון ירד ל-0.21% לשבוע — מתחת לרף.",
    },
  },
  {
    id: "golden",
    short: "צלב הזהב",
    name: "צלב הזהב 50/200",
    source: "חציית ממוצעים נעים פשוטים 50 ו-200",
    rules: "קנייה: ממוצע 50 חוצה מעל ממוצע 200. יציאה: ממוצע 50 חוצה מתחת לממוצע 200.",
    sides: "long",
    warmup: 200,
    run(candles) {
      const closes = candles.map((c) => c.close);
      const fast = sma(closes, 50);
      const slow = sma(closes, 200);
      return runOnClose(candles, 200, (i, side) => {
        if (side === 0) return crossUp(fast, slow, i) ? 1 : 0;
        return crossDown(fast, slow, i) ? 0 : side;
      });
    },
  },
  {
    id: "macd",
    short: "MACD",
    name: "חציית MACD",
    source: "Gerald Appel — MACD(12,26,9)",
    rules: "קנייה: קו ה-MACD חוצה מעל קו האות. יציאה: חוצה מתחתיו.",
    sides: "long",
    warmup: 35,
    run(candles) {
      const closes = candles.map((c) => c.close);
      const fast = ema(closes, 12);
      const slow = ema(closes, 26);
      const line = fast.map((v, i) => v - slow[i]);
      const signal = ema(line, 9);
      return runOnClose(candles, 35, (i, side) => {
        if (side === 0) return crossUp(line, signal, i) ? 1 : 0;
        return crossDown(line, signal, i) ? 0 : side;
      });
    },
  },
  {
    id: "bollinger",
    short: "בולינגר",
    name: "חזרה לממוצע — בולינגר",
    source: "John Bollinger — Bollinger Bands(20,2)",
    rules: "קנייה: סגירה מתחת לרצועה התחתונה. יציאה: סגירה מעל הממוצע האמצעי (20).",
    sides: "long",
    warmup: 20,
    run(candles) {
      const closes = candles.map((c) => c.close);
      const mid = sma(closes, 20);
      const std = rollingStd(closes, 20, mid);
      return runOnClose(candles, 19, (i, side) => {
        if (side === 0) return closes[i] < mid[i] - 2 * std[i] ? 1 : 0;
        return closes[i] > mid[i] ? 0 : side;
      });
    },
  },
  {
    id: "supertrend",
    short: "סופרטרנד",
    name: "סופרטרנד",
    source: "Olivier Seban — Supertrend(10,3)",
    rules: "לונג כשהסגירה עוברת מעל הקו העליון, שורט כשהיא יורדת מתחת לקו התחתון — תמיד בפנים.",
    sides: "both",
    warmup: 11,
    run(candles) {
      const n = atr(candles, 10);
      const up = [];
      const dn = [];
      const trend = [];
      for (let i = 0; i < candles.length; i++) {
        const c = candles[i];
        if (n[i] == null) {
          up.push(null);
          dn.push(null);
          trend.push(1);
          continue;
        }
        const hl2 = (c.high + c.low) / 2;
        const up1 = up[i - 1] ?? hl2 - 3 * n[i];
        const dn1 = dn[i - 1] ?? hl2 + 3 * n[i];
        const prevClose = candles[i - 1].close;
        up.push(prevClose > up1 ? Math.max(hl2 - 3 * n[i], up1) : hl2 - 3 * n[i]);
        dn.push(prevClose < dn1 ? Math.min(hl2 + 3 * n[i], dn1) : hl2 + 3 * n[i]);
        const prev = trend[i - 1];
        trend.push(prev === -1 && c.close > dn1 ? 1 : prev === 1 && c.close < up1 ? -1 : prev);
      }
      // Starts on the first flip, then always holds the trend's side.
      return runOnClose(candles, 11, (i, side) => {
        if (side === 0) return trend[i] !== trend[i - 1] ? trend[i] : 0;
        return trend[i];
      });
    },
  },
];

export function marketOf(symbol) {
  if (/=X$/.test(symbol)) return "forex";
  if (/-USD$/.test(symbol)) return "crypto";
  if (/=F$/.test(symbol)) return "futures";
  return "stock";
}

// Cost per side (commission + spread/slippage), by the kind of symbol.
export function costFor(symbol) {
  return { forex: 0.0001, crypto: 0.001, futures: 0.0002, stock: 0.0005 }[marketOf(symbol)];
}

// Only strategies that cleared 0.5% a week (compounded, after costs) in the research
// backtest of 2026-10-23 have `verified`, and only those are offered. They run on any chart
// (the user asked for that); passedOn() says whether this chart is the kind where they did.
// Tested: all of STRATEGIES on 1h/4h/1d/1w over 13 ETFs, 30 large US stocks, 6 forex pairs,
// 16 coins, gold and oil (1h/4h: ~2.9 years, 1d: 10 years, 1w: 20 years). On stocks, ETFs,
// forex and futures none came close — the best medians were ~0.1% a week, below buy-and-hold.
export function passedOn(strategy, symbol, timeframe) {
  return strategy.verified?.market === marketOf(symbol) && strategy.verified.timeframe === timeframe;
}

export function getStrategy(id) {
  return STRATEGIES.find((s) => s.id === id) || null;
}

// Trades with their % result after costs (the open trade marked at the last close), and
// the summary: total and per-week compounded return, win rate, worst drawdown, time in
// the market, and buy-and-hold over the same stretch for comparison.
export function backtest(candles, strategy, cost = 0) {
  const n = candles.length;
  const start = Math.min(strategy.warmup, n - 1);
  if (n < 2 || start < 0) return null;
  const last = candles[n - 1];
  const { trades: raw, signal } = strategy.run(candles);
  const trades = raw.map((t) => {
    const exitPrice = t.exitPrice ?? last.close;
    const pct = (t.side * (exitPrice - t.entryPrice)) / t.entryPrice - 2 * cost;
    return { ...t, open: t.exitIdx == null, pct };
  });

  // Drawdown is measured candle by candle (closes inside a trade count too).
  let equity = 1;
  let peak = 1;
  let maxDrawdown = 0;
  let barsIn = 0;
  for (const t of trades) {
    const lastIdx = t.exitIdx ?? n - 1;
    for (let i = t.entryIdx; i < lastIdx; i++) {
      const marked = equity * (1 + (t.side * (candles[i].close - t.entryPrice)) / t.entryPrice - cost);
      maxDrawdown = Math.max(maxDrawdown, 1 - marked / peak);
    }
    equity *= Math.max(0, 1 + t.pct);
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, 1 - equity / peak);
    barsIn += (t.exitIdx ?? n) - t.entryIdx;
  }
  const weeks = Math.max((last.time - candles[start].time) / WEEK_SECONDS, 1 / 7);
  const perWeek = (total) => Math.pow(Math.max(total, 1e-9), 1 / weeks) - 1;
  const closed = trades.filter((t) => !t.open);
  const holdTotal = last.close / candles[start].close;
  return {
    trades,
    signal,
    stats: {
      trades: closed.length,
      winRate: closed.length ? closed.filter((t) => t.pct > 0).length / closed.length : null,
      totalReturn: equity - 1,
      weeklyReturn: perWeek(equity),
      maxDrawdown,
      exposure: n - start > 0 ? barsIn / (n - start) : 0,
      weeks,
      holdReturn: holdTotal - 1,
      holdWeekly: perWeek(holdTotal),
    },
  };
}
