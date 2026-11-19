// Draws the panel's return levels (returnLevels.js) on the chart as a plain grey box over
// the move's first candle's body (the user's ask, 15/11/26, in place of the green / red
// lines): one still waiting runs from where the move started to the last candle (15/11/26), with
// its price on the price scale; one the price came back to is fainter and ends at that candle.

const COLORS = { waiting: "rgba(70, 70, 76, 0.4)", returned: "rgba(70, 70, 76, 0.18)", label: "#46464c" };
// A first candle with almost no body still gets a box you can see.
const MIN_HEIGHT_PX = 4;

export class ReturnLevelsPrimitive {
  _chart = null;
  _series = null;
  _requestUpdate = null;
  // [{ dir, price, edge, fromIdx, returnIdx | null }], indices of the chart's own candles.
  _levels = [];
  _digits = 2;
  _lastIdx = 0;

  constructor() {
    const renderer = { draw: (target) => target.useMediaCoordinateSpace((scope) => this._draw(scope)) };
    this._paneViews = [{ zOrder: () => "normal", renderer: () => renderer }];
    this._axisViews = [];
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

  priceAxisViews() {
    return this._axisViews;
  }

  // lastIdx: the chart's last candle, where the waiting boxes end.
  setLevels(levels, digits, lastIdx) {
    this._levels = levels;
    this._digits = digits;
    this._lastIdx = lastIdx;
    this._axisViews = levels
      .filter((l) => l.returnIdx == null)
      .map((l) => ({
        coordinate: () => this._series?.priceToCoordinate(l.price) ?? -100,
        text: () => l.price.toFixed(this._digits),
        textColor: () => "#ffffff",
        backColor: () => COLORS.label,
      }));
    this._requestUpdate?.();
  }

  _draw({ context: ctx }) {
    if (!this._chart || !this._series) return;
    const timeScale = this._chart.timeScale();
    const halfBar = timeScale.options().barSpacing / 2;
    ctx.save();
    for (const l of this._levels) {
      const y1 = this._series.priceToCoordinate(l.price);
      const y2 = this._series.priceToCoordinate(l.edge);
      const from = timeScale.logicalToCoordinate(l.fromIdx);
      const waiting = l.returnIdx == null;
      const to = timeScale.logicalToCoordinate(waiting ? this._lastIdx : l.returnIdx);
      if ([from, to, y1, y2].some((v) => v == null || !Number.isFinite(v))) continue;
      const x1 = from - halfBar;
      const x2 = to + halfBar;
      // Grown away from the level, so the box's edge stays on the price it waits for.
      const h = Math.max(Math.abs(y2 - y1), MIN_HEIGHT_PX);
      const top = l.dir === "down" ? y1 : y1 - h;
      ctx.fillStyle = waiting ? COLORS.waiting : COLORS.returned;
      ctx.fillRect(x1, top, x2 - x1, h);
    }
    ctx.restore();
  }
}
