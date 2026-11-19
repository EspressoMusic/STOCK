// Yahoo's chart API for the live site's serverless functions (Vercel runs api/*.js; a file
// starting with "_" is only a helper). The chart panel (tv.html) on the live site gets its
// candles and prices here, since the backend (backend/app/candles.py) runs only on the PC.

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"];

export async function chart(symbol, interval, range) {
  let error;
  for (const host of HOSTS) {
    try {
      const url = `https://${host}/v8/finance/chart/${encodeURIComponent(symbol)}?interval=${interval}&range=${range}&includePrePost=false`;
      const res = await fetch(url, { headers: { "User-Agent": UA, Accept: "application/json" } });
      const data = await res.json().catch(() => null);
      const result = data?.chart?.result?.[0];
      if (result) return result;
      error = new Error(data?.chart?.error?.description || `Yahoo ${res.status}`);
    } catch (err) {
      error = err;
    }
  }
  throw error;
}

const round = (v) => Math.round(v * 1e5) / 1e5;
const INTRADAY_SECONDS = { "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1h": 3600 };

// The bars of a chart() result, oldest first, each time once (Yahoo sometimes repeats the
// last one, and the chart needs times strictly rising). Intraday, Yahoo's last point is the
// live price stamped now, e.g. 06:50 on 15m: it goes into the bar it's in, as yfinance does.
export function bars(result) {
  const times = result.timestamp || [];
  const q = result.indicators?.quote?.[0] || {};
  const out = [];
  times.forEach((time, i) => {
    const [open, high, low, close] = [q.open?.[i], q.high?.[i], q.low?.[i], q.close?.[i]];
    if (open == null || high == null || low == null || close == null) return;
    const bar = { time, open: round(open), high: round(high), low: round(low), close: round(close), volume: q.volume?.[i] ?? null };
    if (out.length && out[out.length - 1].time >= time) out[out.length - 1] = bar;
    else out.push(bar);
  });
  const step = INTRADAY_SECONDS[result.meta?.dataGranularity];
  const [prev, last] = out.slice(-2);
  if (step && prev && last && last.time - prev.time < step) {
    out.pop();
    prev.high = Math.max(prev.high, last.high);
    prev.low = Math.min(prev.low, last.low);
    prev.close = last.close;
  }
  return out;
}

const formatters = new Map();

// "2026-11-16": the calendar date of a unix time in this time zone.
export function dateIn(timeZone, time) {
  if (!formatters.has(timeZone)) {
    formatters.set(timeZone, new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }));
  }
  return formatters.get(timeZone).format(new Date(time * 1000));
}

// One bar per run of bars with the same key, stamped with the first one's time (or stampOf).
export function group(list, keyOf, stampOf = (first) => first.time) {
  const out = [];
  let key;
  for (const b of list) {
    const k = keyOf(b);
    const last = out[out.length - 1];
    if (last && k === key) {
      last.high = Math.max(last.high, b.high);
      last.low = Math.min(last.low, b.low);
      last.close = b.close;
      last.volume = (last.volume ?? 0) + (b.volume ?? 0);
    } else {
      out.push({ ...b, time: stampOf(b, k) });
      key = k;
    }
  }
  return out;
}
