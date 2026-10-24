// Draws price zones behind the candles as a series primitive, so they pan and zoom
// with the chart for free: the detected zones and their breakout+retest events (see
// zones.js). The zone trainer (trainer.html) also gives it the user's verdicts on those
// (✓ / ✕) and the boxes they drew, and uses itemAt / pointToData for clicking and drawing;
// its replay (zoneReplay.js) adds what was drawn or erased on the current candle and
// timing lines.

const COLORS = {
  zoneFill: "rgba(37, 120, 235, 0.16)",
  zoneLine: "rgba(37, 120, 235, 0.6)",
  okLine: "rgba(22, 128, 61, 0.95)",
  wrongFill: "rgba(120, 120, 120, 0.08)",
  wrongLine: "rgba(220, 38, 38, 0.85)",
  upFill: "rgba(22, 163, 74, 0.3)",
  upLine: "rgba(22, 163, 74, 0.95)",
  downFill: "rgba(220, 38, 38, 0.26)",
  downLine: "rgba(220, 38, 38, 0.95)",
  markZoneFill: "rgba(147, 51, 234, 0.15)",
  markZoneLine: "rgba(147, 51, 234, 0.95)",
  markBreakFill: "rgba(234, 88, 12, 0.2)",
  markBreakLine: "rgba(234, 88, 12, 0.95)",
  highlight: "#17140c",
  ghostFill: "rgba(107, 114, 128, 0.12)",
  ghostLine: "rgba(75, 85, 99, 0.9)",
};

// candleMarks' tones.
const TONES = {
  up: { fill: COLORS.upFill, line: COLORS.upLine },
  down: { fill: COLORS.downFill, line: COLORS.downLine },
  zone: { fill: COLORS.zoneFill, line: COLORS.zoneLine },
};

// Extra px around a zone that still counts as clicking it — thin zones are hard to hit.
const HIT_SLOP_PX = 8;

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

function pill(ctx, text, x, y, color) {
  const padX = 4;
  const h = 14;
  const w = ctx.measureText(text).width + padX * 2;
  const left = Math.round(x - w / 2);
  const top = Math.round(y - h / 2);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.roundRect(left, top, w, h, 7);
  ctx.fill();
  ctx.fillStyle = "#ffffff";
  ctx.fillText(text, x, y + 0.5);
}

// A pill with a small arrow underneath (or above) pointing at anchorY — names the candle
// right where the arrow touches it.
function callout(ctx, text, x, anchorY, color, above) {
  const gap = 6;
  const h = 14;
  const y = above ? anchorY - gap - h / 2 : anchorY + gap + h / 2;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x - 4, above ? anchorY - gap : anchorY + gap);
  ctx.lineTo(x + 4, above ? anchorY - gap : anchorY + gap);
  ctx.lineTo(x, above ? anchorY - 1 : anchorY + 1);
  ctx.closePath();
  ctx.fill();
  pill(ctx, text, x, y, color);
}

function outline(ctx, r, color, width, dash = []) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dash);
  ctx.strokeRect(r.x, r.y, r.w, r.h);
  ctx.setLineDash([]);
}

export class ZonesPrimitive {
  _chart = null;
  _series = null;
  _requestUpdate = null;
  _font = null;
  _state = {
    candles: [],
    zones: [],
    events: [],
    // Trainer only: detector item id → "ok" | "wrong", the user's boxes
    // ({ id, kind, fromTime, toTime, top, bottom, label }), and the item to emphasise.
    verdicts: {},
    boxes: [],
    highlightId: null,
    // Trainer replay only: ids of items drawn on the current candle, items erased on it
    // ([{ kind: "zone" | "event", item }], drawn as grey ghosts), and vertical lines
    // ([{ idx, label, color }]) at candles worth pointing out.
    flashIds: [],
    ghosts: [],
    lines: [],
    // Trainer, opened from a Telegram alert: the alert's own candles to frame and label
    // ([{ idx, text, tone: "up" | "down" | "zone", above }]).
    candleMarks: [],
    texts: { breakout: "", retest: "", flash: "", erased: "" },
  };

  constructor() {
    const renderer = { draw: (target) => target.useMediaCoordinateSpace((scope) => this._draw(scope)) };
    // The breakout / retest candle marks go above the candles, so their labels aren't
    // hidden behind neighbouring candles.
    const candleMarks = { draw: (target) => target.useMediaCoordinateSpace((scope) => this._drawCandleMarks(scope)) };
    this._paneViews = [
      { zOrder: () => "bottom", renderer: () => renderer },
      { zOrder: () => "top", renderer: () => candleMarks },
    ];
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

  setState(partial) {
    this._state = { ...this._state, ...partial };
    this._requestUpdate?.();
  }

  // Pixel rect of a box spanning bars fromIdx..toIdx (whole bar widths) and top..bottom
  // in price; toIdx = null runs it to the right edge of the pane. Half a bar is added
  // by hand: logicalToCoordinate only maps whole bars (a fractional index comes back 0).
  _rect(fromIdx, toIdx, top, bottom) {
    const timeScale = this._chart.timeScale();
    const halfBar = timeScale.options().barSpacing / 2;
    const from = timeScale.logicalToCoordinate(fromIdx);
    const to = toIdx == null ? null : timeScale.logicalToCoordinate(toIdx);
    const x1 = from == null ? null : from - halfBar;
    // The app's chart hides its time axis, so its width() is 0 — use the pane's width.
    const x2 = toIdx == null ? this._paneWidth() : to == null ? null : to + halfBar;
    const y1 = this._series.priceToCoordinate(top);
    const y2 = this._series.priceToCoordinate(bottom);
    if ([x1, x2, y1, y2].some((v) => v == null || !Number.isFinite(v))) return null;
    return { x: x1, y: y1, w: Math.max(1, x2 - x1), h: Math.max(1, y2 - y1) };
  }

  _paneWidth() {
    return this._chart.paneSize(0).width;
  }

  _zoneRect(zone) {
    return this._rect(zone.startIdx, null, zone.top, zone.bottom);
  }

  _eventRect(ev) {
    return this._rect(ev.breakoutIdx, ev.retestIdx, ev.top, ev.bottom);
  }

  _eventColor(ev) {
    if (this._state.verdicts[ev.id] === "wrong") return COLORS.wrongLine;
    return ev.dir === "up" ? COLORS.upLine : COLORS.downLine;
  }

  // Pixel rect hugging one candle, wick to wick, with a few px to spare.
  _candleRect(idx) {
    const candle = this._state.candles[idx];
    if (!candle) return null;
    const x = this._chart.timeScale().logicalToCoordinate(idx);
    const yHigh = this._series.priceToCoordinate(candle.high);
    const yLow = this._series.priceToCoordinate(candle.low);
    if ([x, yHigh, yLow].some((v) => v == null || !Number.isFinite(v))) return null;
    const half = Math.max(4, this._chart.timeScale().options().barSpacing * 0.45) + 3;
    return { x: x - half, y: yHigh - 4, w: half * 2, h: yLow - yHigh + 8, cx: x };
  }

  // A user box is stored by candle time; map it to the bars currently loaded, or null
  // if it's entirely outside them.
  _boxRect(box) {
    const { candles } = this._state;
    const fromIdx = lowerBound(candles, box.fromTime);
    const toIdx = lowerBound(candles, box.toTime + 1) - 1;
    if (fromIdx >= candles.length || toIdx < 0 || toIdx < fromIdx) return null;
    return this._rect(fromIdx, toIdx, box.top, box.bottom);
  }

  _draw({ context: ctx }) {
    if (!this._chart || !this._series) return;
    const { candles, zones, events, verdicts, boxes, highlightId, flashIds, ghosts, lines, candleMarks, texts } = this._state;
    const flash = new Set(flashIds);

    this._font ??= `700 10px ${getComputedStyle(document.body).fontFamily}`;
    ctx.save();
    ctx.font = this._font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (const zone of zones) {
      const r = this._zoneRect(zone);
      if (!r) continue;
      const verdict = verdicts[zone.id];
      ctx.fillStyle = verdict === "wrong" ? COLORS.wrongFill : COLORS.zoneFill;
      ctx.fillRect(r.x, r.y, r.w, r.h);
      const line = verdict === "ok" ? COLORS.okLine : verdict === "wrong" ? COLORS.wrongLine : COLORS.zoneLine;
      ctx.strokeStyle = line;
      ctx.lineWidth = verdict === "ok" ? 2 : 1;
      ctx.setLineDash(verdict === "wrong" ? [4, 3] : []);
      ctx.beginPath();
      ctx.moveTo(r.x, r.y);
      ctx.lineTo(r.x + r.w, r.y);
      ctx.moveTo(r.x, r.y + r.h);
      ctx.lineTo(r.x + r.w, r.y + r.h);
      ctx.stroke();
      ctx.setLineDash([]);
      if (zone.id === highlightId) outline(ctx, r, COLORS.highlight, 2.5);
      // Touch count at the zone's left end (or the chart's, once that's scrolled off).
      const labelX = Math.max(r.x, 0) + 14;
      const text = verdict === "ok" ? `✓ ×${zone.touches}` : verdict === "wrong" ? "✕" : zone.touches ? `×${zone.touches}` : "";
      ctx.fillStyle = line;
      ctx.fillText(text, labelX, r.y + r.h / 2);
      if (flash.has(zone.id)) {
        outline(ctx, r, COLORS.highlight, 2.5);
        pill(ctx, texts.flash, labelX + 42, r.y + r.h / 2, COLORS.highlight);
      }
    }

    for (const ev of events) {
      const r = this._eventRect(ev);
      if (!r) continue;
      const verdict = verdicts[ev.id];
      const up = ev.dir === "up";
      const color = verdict === "wrong" ? COLORS.wrongLine : up ? COLORS.upLine : COLORS.downLine;
      ctx.fillStyle = verdict === "wrong" ? COLORS.wrongFill : up ? COLORS.upFill : COLORS.downFill;
      ctx.fillRect(r.x, r.y, r.w, r.h);
      // Dashed while the retest is still waiting for its bounce, or once called wrong.
      outline(ctx, r, color, verdict === "ok" ? 3 : 1.5, ev.confirmed && verdict !== "wrong" ? [] : [4, 3]);
      if (ev.id === highlightId) outline(ctx, r, COLORS.highlight, 2.5);
      // A tint behind the breakout and retest candles; their frames and labels are
      // drawn over the candles in _drawCandleMarks.
      ctx.fillStyle = verdict === "wrong" ? COLORS.wrongFill : up ? COLORS.upFill : COLORS.downFill;
      for (const idx of [ev.breakoutIdx, ev.retestIdx]) {
        const c = this._candleRect(idx);
        if (!c) continue;
        ctx.beginPath();
        ctx.roundRect(c.x, c.y, c.w, c.h, 4);
        ctx.fill();
      }
      if (verdict) pill(ctx, verdict === "ok" ? "✓" : "✕", r.x + r.w + 10, r.y + r.h / 2, verdict === "ok" ? COLORS.okLine : COLORS.wrongLine);
      if (flash.has(ev.id)) {
        outline(ctx, r, COLORS.highlight, 2.5);
        pill(ctx, texts.flash, r.x + r.w + (verdict ? 40 : 22), r.y + r.h / 2, COLORS.highlight);
      }
    }

    for (const mark of candleMarks) {
      const c = this._candleRect(mark.idx);
      if (!c) continue;
      ctx.fillStyle = TONES[mark.tone].fill;
      ctx.beginPath();
      ctx.roundRect(c.x, c.y, c.w, c.h, 4);
      ctx.fill();
    }

    for (const { kind, item } of ghosts) {
      const r = kind === "zone" ? this._zoneRect(item) : this._eventRect(item);
      if (!r) continue;
      ctx.fillStyle = COLORS.ghostFill;
      ctx.fillRect(r.x, r.y, r.w, r.h);
      outline(ctx, r, COLORS.ghostLine, 1.5, [3, 3]);
      const x = kind === "zone" ? Math.max(r.x, 0) + 30 : r.x + r.w + 26;
      pill(ctx, texts.erased, x, r.y + r.h / 2, COLORS.ghostLine);
    }

    for (const box of boxes) {
      const r = this._boxRect(box);
      if (!r) continue;
      const isZone = box.kind === "zone";
      ctx.fillStyle = isZone ? COLORS.markZoneFill : COLORS.markBreakFill;
      ctx.fillRect(r.x, r.y, r.w, r.h);
      const line = isZone ? COLORS.markZoneLine : COLORS.markBreakLine;
      outline(ctx, r, line, 1.5, [5, 3]);
      if (box.id === highlightId) outline(ctx, r, COLORS.highlight, 2.5);
      if (box.label) pill(ctx, box.label, r.x + r.w / 2, r.y - 9, line);
    }

    // Only at candles already loaded, so a replay doesn't point into its own future.
    const paneHeight = this._chart.paneSize(0).height;
    lines.forEach((line, n) => {
      if (line.idx >= candles.length) return;
      const x = this._chart.timeScale().logicalToCoordinate(line.idx);
      if (x == null) return;
      ctx.strokeStyle = line.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, paneHeight);
      ctx.stroke();
      ctx.setLineDash([]);
      pill(ctx, line.label, x, 12 + n * 18, line.color);
    });

    ctx.restore();
  }

  // A frame around the breakout candle and the retest candle of each event, each with a
  // label pointing at it: the breakout on the side price broke out to, the retest on the
  // side it came back from (flipped if that would run off the pane).
  _drawCandleMarks({ context: ctx }) {
    if (!this._chart || !this._series) return;
    const { events, candleMarks, texts } = this._state;
    const paneHeight = this._chart.paneSize(0).height;

    this._font ??= `700 10px ${getComputedStyle(document.body).fontFamily}`;
    ctx.save();
    ctx.font = this._font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (const ev of events) {
      const color = this._eventColor(ev);
      const up = ev.dir === "up";
      const marks = [
        { idx: ev.breakoutIdx, text: texts.breakout, above: up },
        { idx: ev.retestIdx, text: texts.retest, above: !up },
      ];
      for (const { idx, text, above } of marks) this._markCandle(ctx, idx, text, color, above, paneHeight);
    }
    for (const mark of candleMarks) this._markCandle(ctx, mark.idx, mark.text, TONES[mark.tone].line, mark.above, paneHeight);

    ctx.restore();
  }

  _markCandle(ctx, idx, text, color, above, paneHeight) {
    const c = this._candleRect(idx);
    if (!c) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(c.x, c.y, c.w, c.h, 4);
    ctx.stroke();
    if (!text) return;
    const roomAbove = c.y > 24;
    const roomBelow = c.y + c.h < paneHeight - 24;
    const putAbove = above ? roomAbove || !roomBelow : !roomBelow && roomAbove;
    callout(ctx, text, c.cx, putAbove ? c.y : c.y + c.h, color, putAbove);
  }

  // What a click at (x, y) — px relative to the chart — lands on: the user's boxes first
  // (drawn on top), then breakout+retest events (they sit inside zones), then zones.
  itemAt(x, y) {
    if (!this._chart || !this._series) return null;
    const inside = (r) =>
      r && x >= r.x - HIT_SLOP_PX && x <= r.x + r.w + HIT_SLOP_PX && y >= r.y - HIT_SLOP_PX && y <= r.y + r.h + HIT_SLOP_PX;
    const { boxes, events, zones } = this._state;
    for (let i = boxes.length - 1; i >= 0; i--) {
      if (inside(this._boxRect(boxes[i]))) return { type: "box", id: boxes[i].id };
    }
    for (const ev of events) {
      if (inside(this._eventRect(ev))) return { type: "event", id: ev.id };
    }
    for (const zone of zones) {
      if (inside(this._zoneRect(zone))) return { type: "zone", id: zone.id };
    }
    return null;
  }

  // The bar and price under a chart pixel; the bar is clamped to the loaded candles.
  pointToData(x, y) {
    const { candles } = this._state;
    if (!this._chart || !this._series || candles.length === 0) return null;
    const logical = this._chart.timeScale().coordinateToLogical(x);
    const price = this._series.coordinateToPrice(y);
    if (logical == null || price == null) return null;
    const idx = Math.min(candles.length - 1, Math.max(0, Math.round(logical)));
    return { idx, time: candles[idx].time, price };
  }
}
