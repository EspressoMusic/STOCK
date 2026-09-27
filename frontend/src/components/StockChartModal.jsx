import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries, LineSeries } from "lightweight-charts";
import { getCandles } from "../api";

const TIMEFRAMES = [
  { value: "15m", label: "15 דקות" },
  { value: "1h", label: "שעה" },
  { value: "4h", label: "4 שעות" },
  { value: "1d", label: "יום" },
];

const EMA_PERIOD = 50;
const EMA_COLOR = "#2962ff";

// Same formula as the backend's technical-scan EMA (pandas .ewm(span, adjust=False).mean()).
function computeEma(candles, period) {
  if (candles.length === 0) return [];
  const alpha = 2 / (period + 1);
  let prev = candles[0].close;
  return candles.map((c, i) => {
    prev = i === 0 ? c.close : c.close * alpha + prev * (1 - alpha);
    return { time: c.time, value: prev };
  });
}

function readThemeColors() {
  const style = getComputedStyle(document.documentElement);
  const get = (name) => style.getPropertyValue(name).trim();
  return {
    bg: get("--surface"),
    text: get("--text"),
    border: get("--border"),
    up: get("--up"),
    down: get("--down"),
  };
}

export default function StockChartModal({ symbol, name, onClose }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const emaSeriesRef = useRef(null);
  const [timeframe, setTimeframe] = useState("1h");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const colors = readThemeColors();
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: colors.bg }, textColor: colors.text },
      grid: {
        vertLines: { color: colors.border },
        horzLines: { color: colors.border },
      },
      timeScale: { borderColor: colors.border, timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: colors.border },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    });
    const emaSeries = chart.addSeries(LineSeries, {
      color: EMA_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: `EMA${EMA_PERIOD}`,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    emaSeriesRef.current = emaSeries;

    const observer = new MutationObserver(() => {
      const c = readThemeColors();
      chart.applyOptions({
        layout: { background: { color: c.bg }, textColor: c.text },
        grid: { vertLines: { color: c.border }, horzLines: { color: c.border } },
        timeScale: { borderColor: c.border },
        rightPriceScale: { borderColor: c.border },
      });
      series.applyOptions({
        upColor: c.up,
        downColor: c.down,
        borderUpColor: c.up,
        borderDownColor: c.down,
        wickUpColor: c.up,
        wickDownColor: c.down,
      });
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getCandles(symbol, timeframe)
      .then((data) => {
        seriesRef.current?.setData(
          data.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }))
        );
        emaSeriesRef.current?.setData(computeEma(data.candles, EMA_PERIOD));
        chartRef.current?.timeScale().fitContent();
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [symbol, timeframe]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="symbol">{symbol}</span>
            {name && <span className="name">{name}</span>}
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        <div className="timeframe-toggle">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.value}
              className={timeframe === tf.value ? "active" : ""}
              onClick={() => setTimeframe(tf.value)}
            >
              {tf.label}
            </button>
          ))}
        </div>

        <div className="chart-modal-body">
          {error && <p className="state-msg chart-state-msg">{error}</p>}
          {loading && !error && <p className="chart-loading">טוען נתוני נרות...</p>}
          <div ref={containerRef} className="chart-container" />
        </div>
      </div>
    </div>
  );
}
