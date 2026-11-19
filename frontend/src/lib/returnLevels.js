// The panel's (tv.html) "Return levels" (the user's ask, 14/11/26): a drop or a rise on the
// chart's timeframe (since 15/11/26; was always 4h) — MIN_RUN+ candles in a row the same way —
// leaves the price it started from (the first candle's open) as a level waiting for the price
// to come back to it. The first later candle that reaches it closes the level.

export const MIN_RUN = 3;
// Only a quick return counts (the user's pick, 15/11/26): the price back at the level within
// this many candles after the move's last one. One still waiting shows only until then.
export const MAX_RETURN_BARS = 10;
// Only a move that breaks out counts (the user's pick, 15/11/26): one of its candles closes
// above the highest high (a rise) or below the lowest low (a drop) of this many candles before it.
export const BREAK_LOOKBACK = 10;

function direction(c) {
  if (c.close > c.open) return "up";
  if (c.close < c.open) return "down";
  return null;
}

// Whether candles[from..to) close beyond the high (up) or low (down) of the lookback candles
// before them; a move without that many candles before it doesn't count.
function breaksOut(candles, from, to, dir, lookback) {
  if (from < lookback) return false;
  const before = candles.slice(from - lookback, from);
  const run = candles.slice(from, to);
  if (dir === "up") {
    const high = Math.max(...before.map((c) => c.high));
    return run.some((c) => c.close > high);
  }
  const low = Math.min(...before.map((c) => c.low));
  return run.some((c) => c.close < low);
}

// candles: sorted candles. Each run of minRun+ same-way candles that breaks out → { dir: "down" | "up",
// price, edge (the first candle's close, so price..edge is its body), fromTime (its first
// candle), endTime (its last), returnTime (the candle that came back to price, or null while
// it's still waiting) }.
export function findReturnLevels(candles, minRun = MIN_RUN, lookback = BREAK_LOOKBACK) {
  const levels = [];
  let i = 0;
  while (i < candles.length) {
    const dir = direction(candles[i]);
    let j = i + 1;
    while (dir && j < candles.length && direction(candles[j]) === dir) j++;
    if (dir && j - i >= minRun && breaksOut(candles, i, j, dir, lookback)) {
      const price = candles[i].open;
      let returnTime = null;
      for (let k = j; k < candles.length; k++) {
        if (dir === "down" ? candles[k].high >= price : candles[k].low <= price) {
          returnTime = candles[k].time;
          break;
        }
      }
      levels.push({ dir, price, edge: candles[i].close, fromTime: candles[i].time, endTime: candles[j - 1].time, returnTime });
    }
    i = j;
  }
  return levels;
}
