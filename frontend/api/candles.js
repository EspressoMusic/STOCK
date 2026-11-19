import { bars, chart, dateIn, group } from "./_yahoo.js";

// GET /api/candles?symbol=&timeframe= on the live site, as backend/app/candles.py builds them:
// 4h = each trading day's 1h bars four at a time, and a forex pair's daily = its 1h bars by
// forex day (17:00 New York to 17:00), since Yahoo's own daily forex bars are dojis.

const MAX_CANDLES = 500;
const FETCH = {
  "5m": ["5m", "60d"],
  "15m": ["15m", "60d"],
  "30m": ["30m", "60d"],
  "1h": ["60m", "6mo"],
  "4h": ["60m", "2y"],
  "1d": ["1d", "2y"],
  "1w": ["1wk", "10y"],
  "1mo": ["1mo", "max"],
};
const DAY = 86400;

function fourHour(list, timeZone) {
  let day = null;
  let n = 0;
  return group(list, (b) => {
    const d = dateIn(timeZone, b.time);
    n = d === day ? n + 1 : 0;
    day = d;
    return `${d}|${Math.floor(n / 4)}`;
  });
}

// The forex day a 1h bar belongs to, as midnight UTC of its date: 17:00 New York plus 7
// hours is the next midnight; Yahoo's hour or so after Friday's close is still Friday.
function forexDay(b) {
  const [y, m, d] = dateIn("America/New_York", b.time + 7 * 3600).split("-").map(Number);
  const midnight = Date.UTC(y, m - 1, d) / 1000;
  return new Date(midnight * 1000).getUTCDay() === 6 ? midnight - DAY : midnight;
}

export default async function handler(req, res) {
  const symbol = String(req.query.symbol || "").toUpperCase();
  const timeframe = String(req.query.timeframe || "1h");
  if (!symbol || !FETCH[timeframe]) {
    res.status(400).json({ detail: "symbol and a valid timeframe are needed" });
    return;
  }
  const forexDaily = timeframe === "1d" && symbol.endsWith("=X");
  const [interval, range] = forexDaily ? FETCH["4h"] : FETCH[timeframe];
  try {
    const result = await chart(symbol, interval, range);
    let list = bars(result);
    if (timeframe === "4h") list = fourHour(list, result.meta?.exchangeTimezoneName || "UTC");
    if (forexDaily) list = group(list, forexDay, (_first, day) => day);
    list = list.slice(-MAX_CANDLES);
    if (!list.length) {
      res.status(404).json({ detail: `No candles for ${symbol}` });
      return;
    }
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=120");
    res.status(200).json({ symbol, timeframe, candles: list });
  } catch (err) {
    res.status(502).json({ detail: `Yahoo: ${err.message}` });
  }
}
