// Why the zone detector (zones.js) didn't draw something the user drew in the zone
// trainer (trainer.html), and which of its rules, set differently, would have. Returns
// facts — candles, prices, counts. Not used by the trainer page: it's for tuning zones.js
// from the saved training charts (GET /api/zone-training), run outside the browser.

import { analyzeZones, DEFAULT_RULES, detectZones } from "./zones";
import { addScores, emptyScore, priceOverlap, reviewChart } from "./zoneTraining";

// Values tried for the detector's internal rules (see DEFAULT_RULES in zones.js).
export const PIVOT_BAR_STEPS = [1, 2, 3, 4, 5];
export const TOLERANCE_STEPS = [0.5, 0.75, 1, 1.5, 2];
const MAX_FIXES = 3;
// Bigger changes than this many steps (summed over both lists) aren't offered as a fix —
// they redraw the whole chart rather than fix one zone.
const MAX_FIX_SIZE = 3;
// A drawn box is approximate: a swing high/low this far outside it (as a fraction of its
// height) still counts as in it.
const BOX_SLACK = 0.15;
// Price visits shown are those within the candles the box was drawn over, plus this many
// on each side — the bounce that started a zone is often just before where it's drawn.
const VISIT_MARGIN_BARS = 10;
// A breakout+retest needs a zone at its price; a detector zone sharing this much of the
// thinner one's height with the user's box is that zone (the way zoneTraining.js matches events).
const BREAKOUT_ZONE_OVERLAP = 0.3;

// First index whose time is >= time (candles are sorted by time).
function lowerBound(candles, time) {
  let lo = 0;
  let hi = candles.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (candles[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

// Every time price came into [bottom, top] and left again: { fromIdx, toIdx, from, to }
// with the side it came from and left by ("above" | "below"; to is null while price is
// still inside at the chart's end), kind "cross" when it left by the other side and
// "touch" otherwise. A touch also has the candle that reached furthest in — where a
// swing high/low would be — as extremeIdx and price, with type "low" when price came down
// to the band and "high" when it came up to it.
function visits(candles, bottom, top) {
  const out = [];
  const sideOf = (price) => (price > top ? "above" : price < bottom ? "below" : null);
  const inBand = (c) => c.high >= bottom && c.low <= top;
  for (let i = 0; i < candles.length; i++) {
    if (!inBand(candles[i])) continue;
    const start = i;
    while (i + 1 < candles.length && inBand(candles[i + 1])) i++;
    const from = sideOf(start > 0 ? candles[start - 1].close : candles[start].open);
    const to = i < candles.length - 1 ? sideOf(candles[i + 1].close) : null;
    if (!from && !to) continue;
    const visit = { fromIdx: start, toIdx: i, from, to, kind: from && to && from !== to ? "cross" : "touch" };
    if (visit.kind === "touch") {
      const type = (from ?? to) === "above" ? "low" : "high";
      let ext = start;
      for (let k = start + 1; k <= i; k++) {
        if (type === "low" ? candles[k].low < candles[ext].low : candles[k].high > candles[ext].high) ext = k;
      }
      visit.type = type;
      visit.extremeIdx = ext;
      visit.price = type === "low" ? candles[ext].low : candles[ext].high;
    }
    out.push(visit);
  }
  return out;
}

// Why candle i isn't a swing high/low of type, mirroring findPivots in zones.js: the
// nearest candle within pivotBars that reached as far (an earlier one wins a tie), or
// the chart's edge being nearer than pivotBars. null if it is one.
function notPivotReason(candles, i, type, pivotBars) {
  const value = (j) => (type === "low" ? -candles[j].low : candles[j].high);
  for (let k = 1; k <= pivotBars; k++) {
    if (i - k >= 0 && value(i - k) >= value(i)) {
      return { reason: "beaten", by: i - k, bars: k, side: "before", tie: value(i - k) === value(i) };
    }
    if (i + k < candles.length && value(i + k) > value(i)) {
      return { reason: "beaten", by: i + k, bars: k, side: "after", tie: false };
    }
  }
  if (i < pivotBars) return { reason: "edge-start" };
  if (i + pivotBars >= candles.length) return { reason: "edge-end", barsAfter: candles.length - 1 - i };
  return null;
}

const pivotKey = (p) => `${p.idx}:${p.type}`;

function inBox(box, price) {
  const slack = BOX_SLACK * (box.top - box.bottom);
  return price >= box.bottom - slack && price <= box.top + slack;
}

// Why the detector, run with rules, draws nothing matching the user's zone box [bottom, top]:
// - cause.type "found": it does (with these rules)
// - "none": no swing high/low of its lands in the box
// - "too-few": count of them, fewer than need
// - "spread": count is enough, but no ±window around one price holds need of them —
//   maxGroup is the most it could put together, spread the distance they cover
// - "other-zone": they went into zone, which shares only overlap of the box's height
// - "dropped": they formed a zone of count touches, dropped for touching zone
// - "capped": the detector hit its limit of zones first
function zoneCause(candles, analysis, rules, mark, found) {
  const { bottom, top } = mark;
  if (found) return { type: "found" };
  const inBand = analysis.pivots.filter((p) => inBox(mark, p.price));
  if (!inBand.length) return { type: "none" };

  const clusterOf = new Map();
  for (const cluster of analysis.clusters) for (const p of cluster.removed) clusterOf.set(pivotKey(p), cluster);
  const taken = inBand.map((p) => clusterOf.get(pivotKey(p))).filter(Boolean);
  const intoZone = taken.find((c) => c.zoneId);
  if (intoZone) {
    const zone = analysis.zones.find((z) => z.id === intoZone.zoneId);
    return { type: "other-zone", zone, overlap: priceOverlap(zone, mark) };
  }
  const dropped = taken.find((c) => c.overlapId);
  if (dropped) {
    return {
      type: "dropped",
      count: dropped.members.length,
      zone: analysis.zones.find((z) => z.id === dropped.overlapId),
    };
  }

  const reach = (p) => rules.tolerance * analysis.candle * p.price;
  const maxGroup = Math.max(
    ...inBand.map((p) => analysis.leftover.filter((q) => Math.abs(q.price - p.price) <= reach(p)).length)
  );
  const need = rules.minTouches;
  if (inBand.length < need) return { type: "too-few", count: inBand.length, need };
  if (maxGroup >= need) return { type: "capped" };
  const prices = inBand.map((p) => p.price);
  return {
    type: "spread",
    count: inBand.length,
    need,
    maxGroup,
    spread: Math.max(...prices) - Math.min(...prices),
    window: rules.tolerance * analysis.candle * ((top + bottom) / 2),
  };
}

// Everything the trainer says about why mark (a box the user says is there) isn't drawn
// with rules:
// - found: the detector does draw it
// - cause: see zoneCause; for a breakout+retest mark with a detector zone at its price,
//   { type: "breakout-on-zone", zone } — the zone is there, the event isn't
// - breakoutNeedsZone: a breakout+retest mark with no zone at its price, so cause is why
//   that zone is missing
// - touches: each time price visited the box around the candles the user drew it over
//   (see visits), numbered n from 1, with for a touch: pivot (the detector counts it as
//   a swing high/low), inside (that swing is inside the box) and why (notPivotReason)
// - elsewhere: swing highs/lows in the box outside the candles touches covers
export function explainMiss(candles, mark, rules = {}) {
  const full = { ...DEFAULT_RULES, ...rules };
  const analysis = analyzeZones(candles, full);
  if (!analysis) return null;
  const found = reviewChart(candles, analysis, [mark]).missed.length === 0;

  const fromIdx = lowerBound(candles, mark.fromTime) - VISIT_MARGIN_BARS;
  const toIdx = lowerBound(candles, mark.toTime + 1) - 1 + VISIT_MARGIN_BARS;
  const pivots = new Set(analysis.pivots.map(pivotKey));
  const touches = visits(candles, mark.bottom, mark.top)
    .filter((v) => v.toIdx >= fromIdx && v.fromIdx <= toIdx)
    .map((v, i) => {
      if (v.kind === "cross") return { ...v, n: i + 1 };
      const pivot = pivots.has(pivotKey({ idx: v.extremeIdx, type: v.type }));
      return {
        ...v,
        n: i + 1,
        pivot,
        inside: inBox(mark, v.price),
        why: pivot ? null : notPivotReason(candles, v.extremeIdx, v.type, full.pivotBars),
      };
    });
  const elsewhere = analysis.pivots.filter((p) => inBox(mark, p.price) && (p.idx < fromIdx || p.idx > toIdx)).length;

  let cause;
  let breakoutNeedsZone = false;
  if (mark.kind === "breakout_retest" && !found) {
    const zone = analysis.zones.find((z) => priceOverlap(z, mark) >= BREAKOUT_ZONE_OVERLAP);
    if (zone) cause = { type: "breakout-on-zone", zone };
    else {
      breakoutNeedsZone = true;
      cause = zoneCause(candles, analysis, full, mark, false);
    }
  } else {
    cause = zoneCause(candles, analysis, full, mark, found);
  }
  return { rules: full, found, cause, breakoutNeedsZone, touches, elsewhere };
}

// The trainer's score over every saved chart ([{ candles, marks }]) with rules.
export function scoreAll(charts, rules) {
  return charts.reduce(
    (total, c) => addScores(total, reviewChart(c.candles, detectZones(c.candles, rules), c.marks).score),
    emptyScore()
  );
}

// Rule changes, up to MAX_FIX_SIZE steps, that would make the detector draw mark,
// smallest change first — at most
// MAX_FIXES, each { rules, zonesHere (zones it draws on this chart), after (scoreAll
// over charts) } — plus before (scoreAll with the current rules), zonesNow, and
// fewerTouches: whether one less minTouches (the user's own setting) would find it.
export function findFixes(candles, mark, rules, charts) {
  const current = { ...DEFAULT_RULES, ...rules };
  const finds = (r) => reviewChart(candles, detectZones(candles, r), [mark]).missed.length === 0;
  const step = (list, value) => {
    const exact = list.indexOf(value);
    return exact !== -1 ? exact : list.findIndex((v) => v > value);
  };
  const pivotStep = step(PIVOT_BAR_STEPS, current.pivotBars);
  const toleranceStep = step(TOLERANCE_STEPS, current.tolerance);

  const candidates = [];
  for (const pivotBars of PIVOT_BAR_STEPS) {
    for (const tolerance of TOLERANCE_STEPS) {
      if (pivotBars === current.pivotBars && tolerance === current.tolerance) continue;
      const r = { ...current, pivotBars, tolerance };
      const size =
        Math.abs(PIVOT_BAR_STEPS.indexOf(pivotBars) - pivotStep) + Math.abs(TOLERANCE_STEPS.indexOf(tolerance) - toleranceStep);
      if (size > MAX_FIX_SIZE || !finds(r)) continue;
      candidates.push({ rules: r, size, zonesHere: detectZones(candles, r).zones.length });
    }
  }
  candidates.sort((a, b) => a.size - b.size || a.zonesHere - b.zonesHere);

  return {
    fixes: candidates.slice(0, MAX_FIXES).map((c) => ({ ...c, after: scoreAll(charts, c.rules) })),
    before: scoreAll(charts, current),
    zonesNow: detectZones(candles, current).zones.length,
    fewerTouches: current.minTouches > 2 && finds({ ...current, minTouches: current.minTouches - 1 }),
  };
}
