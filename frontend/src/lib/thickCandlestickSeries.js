import { customSeriesDefaultOptions } from "lightweight-charts";

// Built-in CandlestickSeries has no way to control border/wick thickness (fixed ~1px).
// This custom series draws candles manually so the border and wicks can be made thicker.
const defaultOptions = {
  ...customSeriesDefaultOptions,
  upColor: "#26a69a",
  downColor: "#ef5350",
  borderColor: "#000000",
  wickColor: "#000000",
  borderWidth: 3,
  wickWidth: 3,
  barWidthFactor: 0.8,
};

class ThickCandlestickRenderer {
  _data = null;
  _options = null;

  update(data, options) {
    this._data = data;
    this._options = options;
  }

  draw(target, priceToCoordinate) {
    target.useBitmapCoordinateSpace((scope) => this._drawImpl(scope, priceToCoordinate));
  }

  _drawImpl(scope, priceToCoordinate) {
    const data = this._data;
    const options = this._options;
    if (!data || !options || !data.visibleRange || data.bars.length === 0) return;

    const { context: ctx, horizontalPixelRatio: hRatio, verticalPixelRatio: vRatio } = scope;

    const bodyWidthBitmap = Math.max(1, Math.round(data.barSpacing * options.barWidthFactor * hRatio));
    // Cap the border relative to the body width so it never swallows the fill color entirely
    // on a tight zoom (e.g. a default view of ~40 narrow bars on a phone-width chart).
    const borderWidthBitmap = Math.max(1, Math.min(Math.round(options.borderWidth * hRatio), Math.floor(bodyWidthBitmap * 0.35)));
    const wickWidthBitmap = Math.max(1, Math.round(options.wickWidth * hRatio));

    for (let i = data.visibleRange.from; i < data.visibleRange.to; i++) {
      const bar = data.bars[i];
      const { open, high, low, close } = bar.originalData;
      const isUp = close >= open;

      const openY = priceToCoordinate(open);
      const closeY = priceToCoordinate(close);
      const highY = priceToCoordinate(high);
      const lowY = priceToCoordinate(low);
      if (openY === null || closeY === null || highY === null || lowY === null) continue;

      const xBitmap = Math.round(bar.x * hRatio);
      const openYBitmap = openY * vRatio;
      const closeYBitmap = closeY * vRatio;
      const highYBitmap = highY * vRatio;
      const lowYBitmap = lowY * vRatio;

      ctx.fillStyle = options.wickColor;
      const wickX = xBitmap - Math.floor(wickWidthBitmap / 2);
      ctx.fillRect(wickX, highYBitmap, wickWidthBitmap, Math.max(1, lowYBitmap - highYBitmap));

      const top = Math.min(openYBitmap, closeYBitmap);
      const bodyHeight = Math.max(Math.abs(closeYBitmap - openYBitmap), borderWidthBitmap);
      const bodyX = xBitmap - Math.floor(bodyWidthBitmap / 2);

      ctx.fillStyle = isUp ? options.upColor : options.downColor;
      ctx.fillRect(bodyX, top, bodyWidthBitmap, bodyHeight);

      ctx.strokeStyle = options.borderColor;
      ctx.lineWidth = borderWidthBitmap;
      ctx.strokeRect(
        bodyX + borderWidthBitmap / 2,
        top + borderWidthBitmap / 2,
        Math.max(1, bodyWidthBitmap - borderWidthBitmap),
        Math.max(1, bodyHeight - borderWidthBitmap)
      );
    }
  }
}

export class ThickCandlestickSeries {
  _renderer = new ThickCandlestickRenderer();

  priceValueBuilder(plotRow) {
    return [plotRow.high, plotRow.low, plotRow.close];
  }

  isWhitespace(data) {
    return data.close === undefined;
  }

  renderer() {
    return this._renderer;
  }

  update(data, options) {
    this._renderer.update(data, options);
  }

  defaultOptions() {
    return defaultOptions;
  }
}
