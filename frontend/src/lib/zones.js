// Price zones the market turned at several times, plus breakout-and-retest events on
// them. Pure functions over the chart's candles ({ time, open, high, low, close }).
//
// Bump ZONES_ALGO_VERSION whenever the rules below change, so marks saved in the zone
// trainer (trainer.html) record which version of the algorithm the user was correcting.
export const ZONES_ALGO_VERSION = 3;

// Sizes below are in "typical candles": the median candle range as a fraction of price,
// times the price where it's applied — so a stock that ran from $0.60 to $3 gets zones
// sized for each price level rather than one dollar width for both.
export const PIVOT_BARS = 3; // a swing high/low must beat this many bars on each side
const TOLERANCE = 0.5; // pivots within ±this many typical candles of a zone's centre are touches of it
// The rules the zone trainer can try other values of; these defaults are what the app draws.
export const DEFAULT_RULES = { minTouches: 3, pivotBars: PIVOT_BARS, tolerance: TOLERANCE };
const MIN_HEIGHT = 0.3; // thinnest zone drawn
const BREAK_BUFFER = 0.1; // a close must clear the zone by this much to count as a breakout
const RETEST_REACH = 0.25; // how near a pullback must come back to the zone to count as a retest
const LEAVE = 1; // how far past the zone price must close after a breakout before a pullback counts
const FAST_RETEST_BARS = 3; // a retest this soon after the breakout needn't wait for LEAVE (see findRetest)
const MAX_RETEST_BARS = 30;
const CONFIRM_BARS = 5;
// Zones far from the price on screen are simply off-chart, so this only guards against
// a pathological pile-up rather than picking the "best" few.
const MAX_ZONES = 16;

const EMPTY = { zones: [], events: [] };

// Median true range as a fraction of the close — the "typical candle", robust to the
// odd gap or news bar.
export function typicalCandle(candles) {
  const ranges = candles
    .map((c, i) => {
      const prevClose = i > 0 ? candles[i - 1].close : c.open;
      return (Math.max(c.high, prevClose) - Math.min(c.low, prevClose)) / c.close;
    })
    .filter((r) => r > 0)
    .sort((a, b) => a - b);
  return ranges.length ? ranges[Math.floor(ranges.length / 2)] : 0;
}

// Swing highs/lows. Ties count for the earlier bar only, so a flat double top is one pivot.
function findPivots(candles, pivotBars) {
  const pivots = [];
  for (let i = pivotBars; i < candles.length - pivotBars; i++) {
    let isHigh = true;
    let isLow = true;
    for (let k = 1; k <= pivotBars; k++) {
      if (candles[i - k].high >= candles[i].high || candles[i + k].high > candles[i].high) isHigh = false;
      if (candles[i - k].low <= candles[i].low || candles[i + k].low < candles[i].low) isLow = false;
    }
    if (isHigh) pivots.push({ idx: i, price: candles[i].high, type: "high" });
    if (isLow) pivots.push({ idx: i, price: candles[i].low, type: "low" });
  }
  return pivots;
}

// Repeatedly takes the price with the most pivots around it as a zone, then removes
// those pivots, until no price has minTouches left. Highs and lows mix on purpose:
// old resistance that turns into support is the same zone. Every pick is also kept in
// clusters — with the pivots it removed and, when it was dropped for overlapping a zone
// already taken, which one — so the trainer can say why a zone the user drew isn't here.
function clusterZones(candles, pivots, candle, { minTouches, tolerance }) {
  let remaining = pivots;
  const zones = [];
  const clusters = [];
  while (remaining.length >= minTouches && zones.length < MAX_ZONES) {
    let best = null;
    for (const p of remaining) {
      const reach = tolerance * candle * p.price;
      const members = remaining.filter((q) => Math.abs(q.price - p.price) <= reach);
      if (!best || members.length > best.length) best = members;
    }
    if (best.length < minTouches) break;

    let bottom = Math.min(...best.map((p) => p.price));
    let top = Math.max(...best.map((p) => p.price));
    const unit = candle * ((top + bottom) / 2); // one typical candle at this zone's price
    if (top - bottom < MIN_HEIGHT * unit) {
      const mid = (top + bottom) / 2;
      bottom = mid - (MIN_HEIGHT * unit) / 2;
      top = mid + (MIN_HEIGHT * unit) / 2;
    }
    const exclusion = (tolerance * unit) / 2;
    const removed = remaining.filter((p) => p.price >= bottom - exclusion && p.price <= top + exclusion);
    remaining = remaining.filter((p) => p.price < bottom - exclusion || p.price > top + exclusion);
    const overlap = zones.find((z) => bottom <= z.top && top >= z.bottom);
    const cluster = { members: best, removed, bottom, top, zoneId: null, overlapId: overlap?.id ?? null };
    clusters.push(cluster);
    if (overlap) continue;

    const touchIdx = best.map((p) => p.idx).sort((a, b) => a - b);
    cluster.zoneId = `${candles[touchIdx[0]].time}:${bottom.toPrecision(6)}`;
    zones.push({
      id: cluster.zoneId,
      bottom,
      top,
      unit,
      startIdx: touchIdx[0],
      touches: touchIdx.length,
      touchIdx,
      touchTimes: touchIdx.map((i) => candles[i].time),
    });
  }
  return { zones, clusters, leftover: remaining };
}

// After a breakout candle at b (dir "up" | "down"): price has to close at least
// LEAVE typical candles clear of the zone, then the retest is the first candle that
// reaches back to the zone and closes on the breakout side of its middle. It's confirmed
// when a later close (within CONFIRM_BARS) gets past the retest candle's far end; a close
// back through the zone at any point means the breakout failed. A retest too recent to
// have been confirmed or failed yet comes back as unconfirmed.
// A quick retest — the 2nd to FAST_RETEST_BARS-th candle after the breakout, before price
// got clear — counts too, but only as a real pullback (past the previous candle's extreme,
// back to the zone itself) whose confirming close is also LEAVE clear of the zone: the
// candle right after a breakout nearly always overlaps the zone, and price hugging it
// after a weak break is otherwise just chop.
function findRetest(candles, zone, b, dir, buffer, reach) {
  const up = dir === "up";
  const mid = (zone.top + zone.bottom) / 2;
  const leave = LEAVE * zone.unit;
  const failed = (c) => (up ? c.close < zone.bottom - buffer : c.close > zone.top + buffer);
  const holds = (c) => (up ? c.close > mid : c.close < mid);
  const clear = (c) => (up ? c.close > zone.top + leave : c.close < zone.bottom - leave);
  // "confirmed" | "failed" | "pending" (too recent to tell yet). mustClear: the
  // confirming close has to be LEAVE clear of the zone too (quick retests).
  const confirm = (j, mustClear) => {
    const c = candles[j];
    const confirmEnd = Math.min(candles.length - 1, j + CONFIRM_BARS);
    for (let k = j + 1; k <= confirmEnd; k++) {
      if (failed(candles[k])) return "failed";
      const past = up ? candles[k].close > c.high : candles[k].close < c.low;
      if (past && (!mustClear || clear(candles[k]))) return "confirmed";
    }
    return j + CONFIRM_BARS > candles.length - 1 ? "pending" : "failed";
  };
  const end = Math.min(candles.length - 1, b + MAX_RETEST_BARS);
  let left = false;
  for (let j = b + 1; j <= end; j++) {
    const c = candles[j];
    if (failed(c)) return null;
    if (!left) {
      const pullback = up
        ? c.low < candles[j - 1].low && c.low <= zone.top
        : c.high > candles[j - 1].high && c.high >= zone.bottom;
      if (j - b >= 2 && j - b <= FAST_RETEST_BARS && pullback && holds(c) && confirm(j, true) === "confirmed") {
        return { retestIdx: j, confirmed: true };
      }
      left = clear(c);
      continue;
    }
    const reached = up ? c.low <= zone.top + reach : c.high >= zone.bottom - reach;
    if (!reached || !holds(c)) continue;

    const outcome = confirm(j, false);
    return outcome === "failed" ? null : { retestIdx: j, confirmed: outcome === "confirmed" };
  }
  return null;
}

// A breakout is a close past the zone on the opposite side from the previous close
// outside it, once the zone already has at least two touches.
function findBreakRetests(candles, zone) {
  const buffer = BREAK_BUFFER * zone.unit;
  const reach = RETEST_REACH * zone.unit;
  const events = [];
  let side = null;
  let touchesBefore = 0;
  for (let i = zone.startIdx; i < candles.length; i++) {
    while (touchesBefore < zone.touchIdx.length && zone.touchIdx[touchesBefore] < i) touchesBefore++;
    const c = candles[i];
    const now = c.close > zone.top + buffer ? "above" : c.close < zone.bottom - buffer ? "below" : null;
    if (now && side && now !== side && touchesBefore >= 2) {
      const dir = now === "above" ? "up" : "down";
      const retest = findRetest(candles, zone, i, dir, buffer, reach);
      if (retest) {
        events.push({
          id: `${zone.id}>${c.time}`,
          zoneId: zone.id,
          dir,
          confirmed: retest.confirmed,
          bottom: zone.bottom,
          top: zone.top,
          breakoutIdx: i,
          retestIdx: retest.retestIdx,
          breakoutTime: c.time,
          retestTime: candles[retest.retestIdx].time,
        });
      }
    }
    if (now) side = now;
  }
  return events;
}

// A candle that closes through several stacked zones at once is one breakout, so it gets
// one retest: the first pullback to any of them (the stronger zone on a tie — zones come
// strongest first).
function oneRetestPerBreakout(events) {
  const kept = new Map();
  for (const ev of events) {
    const key = `${ev.breakoutIdx}:${ev.dir}`;
    const prev = kept.get(key);
    if (!prev || ev.retestIdx < prev.retestIdx) kept.set(key, ev);
  }
  return [...kept.values()];
}

// detectZones plus how it got there: the typical candle, every pivot, every cluster it
// picked (see clusterZones) and the pivots left over. null when there's too little to go on.
export function analyzeZones(candles, rules = {}) {
  const { minTouches, pivotBars, tolerance } = { ...DEFAULT_RULES, ...rules };
  if (!candles || candles.length < pivotBars * 2 + 1) return null;
  const candle = typicalCandle(candles);
  if (!(candle > 0)) return null;

  const pivots = findPivots(candles, pivotBars);
  const { zones, clusters, leftover } = clusterZones(candles, pivots, candle, { minTouches, tolerance });
  const events = oneRetestPerBreakout(zones.flatMap((z) => findBreakRetests(candles, z)));
  return { candle, pivots, clusters, leftover, zones, events };
}

// rules: any of DEFAULT_RULES; the app passes only minTouches (the user's setting).
export function detectZones(candles, rules = {}) {
  const analysis = analyzeZones(candles, rules);
  return analysis ? { zones: analysis.zones, events: analysis.events } : EMPTY;
}

// detectZones over candles[fromIdx..] only (the app's start line), with every index
// shifted back so it still points into the full candles array.
export function detectZonesFrom(candles, fromIdx, rules = {}) {
  if (!fromIdx) return detectZones(candles, rules);
  const { zones, events } = detectZones(candles.slice(fromIdx), rules);
  return {
    zones: zones.map((z) => ({ ...z, startIdx: z.startIdx + fromIdx, touchIdx: z.touchIdx.map((i) => i + fromIdx) })),
    events: events.map((e) => ({ ...e, breakoutIdx: e.breakoutIdx + fromIdx, retestIdx: e.retestIdx + fromIdx })),
  };
}
