// Same formula as the backend's technical-scan EMA (pandas .ewm(span, adjust=False).mean()).
export function computeEma(candles, period) {
  if (candles.length === 0) return [];
  const alpha = 2 / (period + 1);
  let prev = candles[0].close;
  return candles.map((c, i) => {
    prev = i === 0 ? c.close : c.close * alpha + prev * (1 - alpha);
    return { time: c.time, value: prev };
  });
}
