// Replays the zone detector (zones.js) over a chart one candle at a time, the way it
// would have run live: frame i is what it draws when only candles 0..i exist. Zones and
// breakout+retest events are followed from frame to frame, so the zone trainer can show
// when each one was first drawn — how many candles after the moment it marks — and which
// ones were erased (and maybe drawn again) along the way.
import { DEFAULT_RULES, detectZones } from "./zones";
import { priceOverlap } from "./zoneTraining";

// A zone is the same zone as an earlier one when their prices share at least this much
// of the thinner one's height (the way zoneTraining.js matches zones).
const SAME_ZONE_OVERLAP = 0.5;
// The zone's edges move a little as candles come in, which can move its breakout and
// retest candles; a breakout+retest on the same zone in the same direction whose breakout
// and retest are each within this many candles of an earlier one's is the same one.
const SAME_EVENT_BARS = 2;
// Candles past the earliest possible drawing that still count as only slightly late.
const SLIGHT_BARS = 2;

// The earliest a thing can be drawn, in candles after the moment it marks: a touch is a
// swing high/low, which is only known pivotBars candles later (the replay's own rules,
// not the default — the trainer can tune it); a retest is known when its candle closes.
function minDelay(kind, pivotBars) {
  return kind === "zone" ? pivotBars : 0;
}

// "ontime" | "slight" | "late", by how far past the earliest possible drawing it came.
export function lateness(track) {
  const extra = track.delay - track.minDelay;
  return extra <= 0 ? "ontime" : extra <= SLIGHT_BARS ? "slight" : "late";
}

function sameZone(track, zone) {
  const overlap = priceOverlap(track.last, zone);
  return overlap >= SAME_ZONE_OVERLAP ? overlap : 0;
}

function sameEvent(track, event, zoneTrack) {
  if (track.zoneTrack !== zoneTrack || track.dir !== event.dir) return 0;
  const gap = Math.max(
    Math.abs(track.last.breakoutIdx - event.breakoutIdx),
    Math.abs(track.last.retestIdx - event.retestIdx)
  );
  return gap <= SAME_EVENT_BARS ? 1 / (1 + gap) : 0;
}

// The best-scoring track for an item: one still drawn on the previous candle first, else
// one drawn before and since erased (so a zone that flickers off and on stays one track).
function findTrack(tracks, i, taken, score) {
  for (const alive of [true, false]) {
    let best = null;
    let bestScore = 0;
    for (const track of tracks) {
      if (taken.has(track) || (track.lastIdx === i - 1) !== alive) continue;
      const s = score(track);
      if (s > bestScore) {
        best = track;
        bestScore = s;
      }
    }
    if (best) return best;
  }
  return null;
}

// Marks track as drawn on candle i: extends its current run, or starts a new one.
function draw(track, i, item) {
  const runs = track.runs;
  if (runs.length && runs[runs.length - 1].to === i - 1) runs[runs.length - 1].to = i;
  else runs.push({ from: i, to: i });
  track.lastIdx = i;
  track.last = item;
}

// Returns:
// - frames[i]: { zones, events, trackOf } — what the detector draws after candle i closes,
//   with trackOf mapping each zone/event id to the track it belongs to
// - tracks: every zone and breakout+retest that was ever drawn, in drawing order:
//   { id, kind: "zone" | "event", appearIdx (first candle it was drawn on), markIdx (the
//   candle it marks: the touch that gave a zone its minTouches, or the retest candle),
//   delay (appearIdx - markIdx), runs ([{ from, to }] candles it was drawn on, inclusive),
//   erasedIdx (candle it was gone for good, or null if still drawn at the end), top,
//   bottom, minDelay (the earliest it could have been drawn, in candles after markIdx),
//   plus touches for zones and dir / breakoutIdx / confirmIdx / zoneTrack for events}
// - changes[i]: the tracks drawn or erased on candle i
// - pivotBars: the swing size the replay ran with
// rules: the detector rules to replay with (see DEFAULT_RULES in zones.js).
export function replayZones(candles, rules) {
  const { minTouches, pivotBars } = { ...DEFAULT_RULES, ...rules };
  const frames = [];
  const zoneTracks = [];
  const eventTracks = [];
  const tracks = [];

  for (let i = 0; i < candles.length; i++) {
    const detection = detectZones(candles.slice(0, i + 1), rules);
    const trackOf = {};
    const zoneTrackOf = {};
    const taken = new Set();

    for (const zone of detection.zones) {
      let track = findTrack(zoneTracks, i, taken, (t) => sameZone(t, zone));
      if (!track) {
        track = {
          id: `z${tracks.length}`,
          kind: "zone",
          appearIdx: i,
          markIdx: zone.touchIdx[Math.min(minTouches, zone.touchIdx.length) - 1],
          top: zone.top,
          bottom: zone.bottom,
          touches: zone.touches,
          runs: [],
        };
        zoneTracks.push(track);
        tracks.push(track);
      }
      taken.add(track);
      draw(track, i, zone);
      trackOf[zone.id] = track.id;
      zoneTrackOf[zone.id] = track;
    }

    for (const event of detection.events) {
      const zoneTrack = zoneTrackOf[event.zoneId];
      let track = findTrack(eventTracks, i, taken, (t) => sameEvent(t, event, zoneTrack));
      if (!track) {
        track = {
          id: `e${tracks.length}`,
          kind: "event",
          appearIdx: i,
          markIdx: event.retestIdx,
          top: event.top,
          bottom: event.bottom,
          dir: event.dir,
          breakoutIdx: event.breakoutIdx,
          confirmIdx: null,
          zoneTrack,
          runs: [],
        };
        eventTracks.push(track);
        tracks.push(track);
      }
      taken.add(track);
      draw(track, i, event);
      if (event.confirmed && track.confirmIdx == null) track.confirmIdx = i;
      trackOf[event.id] = track.id;
    }

    frames.push({ ...detection, trackOf });
  }

  const changes = candles.map(() => []);
  for (const track of tracks) {
    track.delay = track.appearIdx - track.markIdx;
    track.minDelay = minDelay(track.kind, pivotBars);
    track.erasedIdx = track.lastIdx < candles.length - 1 ? track.lastIdx + 1 : null;
    for (const run of track.runs) {
      changes[run.from].push(track);
      if (run.to < candles.length - 1) changes[run.to + 1].push(track);
    }
  }
  return { frames, tracks, changes, pivotBars };
}
