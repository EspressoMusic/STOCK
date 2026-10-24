// Compares what the zone detector (zones.js) drew on a chart with the marks the user
// made in the zone trainer (trainer.html). Marks are kept by geometry — prices and
// candle times — rather than by detector id, so a chart reviewed under one version of
// the detector can be replayed and scored against any later one.
//
// A mark is { id, kind: "zone" | "breakout_retest", source: "bot" | "user",
// verdict: "ok" | "wrong", fromTime, toTime, top, bottom, dir?, algoVersion?, note? }:
// source "bot" is the user's verdict on something the detector drew, source "user" a
// box they drew because the detector missed it (always "ok"). note is the user's own
// words on why it's there.

// How much of the thinner box's height two boxes must share to count as the same thing.
const ZONE_MIN_OVERLAP = 0.5;
const EVENT_MIN_OVERLAP = 0.3;

export function priceOverlap(a, b) {
  const shared = Math.min(a.top, b.top) - Math.max(a.bottom, b.bottom);
  const thinner = Math.min(a.top - a.bottom, b.top - b.bottom);
  return thinner > 0 && shared > 0 ? shared / thinner : 0;
}

// 0 when they aren't the same thing, otherwise how well they overlap (up to 1). Zones
// are price levels, so only price counts — a level price comes back to months later is
// still the same zone, the way the detector clusters it. A breakout+retest is one moment,
// so it also has to share candles with the mark.
function matchScore(box, mark) {
  if (box.kind !== mark.kind) return 0;
  const overlap = priceOverlap(box, mark);
  if (box.kind === "zone") return overlap >= ZONE_MIN_OVERLAP ? overlap : 0;
  if (box.dir && mark.dir && box.dir !== mark.dir) return 0;
  if (box.fromTime > mark.toTime || mark.fromTime > box.toTime) return 0;
  return overlap >= EVENT_MIN_OVERLAP ? overlap : 0;
}

// The geometry of a detector zone or breakout+retest, in the same terms as a mark.
export function zoneBox(zone, candles) {
  return {
    kind: "zone",
    fromTime: zone.touchTimes[0],
    toTime: candles[candles.length - 1].time,
    top: zone.top,
    bottom: zone.bottom,
  };
}

export function eventBox(event) {
  return {
    kind: "breakout_retest",
    fromTime: event.breakoutTime,
    toTime: event.retestTime,
    top: event.top,
    bottom: event.bottom,
    dir: event.dir,
  };
}

// The mark that best matches box, preferring the user's verdict on a detector item over
// a box they drew when both fit equally well. sources limits which marks are considered.
export function bestMark(box, marks, sources = ["bot", "user"]) {
  let best = null;
  let bestScore = 0;
  for (const mark of marks) {
    if (!sources.includes(mark.source)) continue;
    const score = matchScore(box, mark);
    if (score > bestScore || (score > 0 && score === bestScore && mark.source === "bot")) {
      best = mark;
      bestScore = score;
    }
  }
  return best;
}

function emptyTally() {
  return { truth: 0, found: 0, wrong: 0, unreviewed: 0 };
}

// Everything the trainer shows for one chart:
// - verdicts: detector item id → "ok" | "wrong", for items the user has judged
// - missed: "ok" marks no detector item matches (what the detector failed to find)
// - score per kind: truth = things the user says are there, found = how many of them
//   the detector drew, wrong = detector items the user called wrong, unreviewed = ones
//   they haven't judged yet
export function reviewChart(candles, detection, marks) {
  const verdicts = {};
  const score = { zones: emptyTally(), events: emptyTally() };
  if (!candles.length) return { verdicts, missed: [], score };

  const items = [
    ...detection.zones.map((z) => ({ id: z.id, tally: score.zones, box: zoneBox(z, candles) })),
    ...detection.events.map((e) => ({ id: e.id, tally: score.events, box: eventBox(e) })),
  ];
  for (const item of items) {
    const mark = bestMark(item.box, marks);
    if (mark) verdicts[item.id] = mark.verdict;
    if (mark?.verdict === "wrong") item.tally.wrong++;
    if (!mark) item.tally.unreviewed++;
  }

  const missed = [];
  for (const mark of marks) {
    if (mark.verdict !== "ok") continue;
    const tally = mark.kind === "zone" ? score.zones : score.events;
    tally.truth++;
    if (items.some((item) => matchScore(item.box, mark) > 0)) tally.found++;
    else missed.push(mark);
  }
  return { verdicts, missed, score };
}

export function addScores(a, b) {
  const add = (x, y) => ({
    truth: x.truth + y.truth,
    found: x.found + y.found,
    wrong: x.wrong + y.wrong,
    unreviewed: x.unreviewed + y.unreviewed,
  });
  return { zones: add(a.zones, b.zones), events: add(a.events, b.events) };
}

export function emptyScore() {
  return { zones: emptyTally(), events: emptyTally() };
}
