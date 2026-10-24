import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries } from "lightweight-charts";
import { getCandles } from "../api";
import { alertMarking } from "../lib/alertLink";
import { ZonesPrimitive } from "../lib/zonesPrimitive";

// The scanner opened from a Telegram alert's link (see lib/alertLink.js): the alert's
// chart with its own marking — the zone and the breakout + retest or touch candles.

const TIMEFRAME_LABELS = { "1h": "שעה", "4h": "4 שעות", "1d": "יומי", "1w": "שבועי" };
const ZONE_TEXTS = { breakout: "פריצה", retest: "ריטסט" };
// The view starts this many candles before the first marked one, and shows at least
// MIN_ALERT_BARS.
const ALERT_LEAD_BARS = 20;
const MIN_ALERT_BARS = 60;

function readThemeColors() {
  const style = getComputedStyle(document.documentElement);
  const get = (name) => style.getPropertyValue(name).trim();
  return { bg: get("--surface"), text: get("--text"), border: get("--border"), up: get("--up"), down: get("--down") };
}

export default function AlertChartModal({ alert, onClose }) {
  const containerRef = useRef(null);
  const [state, setState] = useState("loading"); // "loading" | null | error message

  useEffect(() => {
    const colors = readThemeColors();
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: colors.bg }, textColor: colors.text, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      timeScale: { borderColor: colors.border, timeVisible: alert.timeframe === "1h" || alert.timeframe === "4h", secondsVisible: false },
      rightPriceScale: { borderColor: colors.border },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      priceLineVisible: false,
    });
    const zones = new ZonesPrimitive();
    series.attachPrimitive(zones);

    let stale = false;
    getCandles(alert.symbol, alert.timeframe)
      .then((data) => {
        if (stale) return;
        const candles = data.candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
        const marks = candles.length ? alertMarking(alert, candles) : null;
        if (!marks) throw new Error("לא נמצאו הנרות של ההתראה");
        if (candles[candles.length - 1].close < 1) {
          series.applyOptions({ priceFormat: { type: "price", precision: 4, minMove: 0.0001 } });
        }
        series.setData(candles);
        zones.setState({ candles, zones: marks.zones, events: marks.events, candleMarks: marks.candleMarks, texts: ZONE_TEXTS });
        // Zoomed in on the alert's candles, so their labels don't run into each other.
        const firstIdx = Math.min(...marks.candleMarks.map((m) => m.idx));
        chart.timeScale().setVisibleLogicalRange({
          from: Math.max(Math.min(firstIdx - ALERT_LEAD_BARS, candles.length - MIN_ALERT_BARS), 0),
          to: candles.length + 3,
        });
        setState(null);
      })
      .catch((err) => !stale && setState(err.message));

    return () => {
      stale = true;
      chart.remove();
    };
  }, [alert]);

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
            <span className="symbol">{alert.symbol}</span>
            <span className="name">
              {TIMEFRAME_LABELS[alert.timeframe]} · הסימון מההתראה
            </span>
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        <div className="chart-modal-body">
          {state === "loading" && <p className="chart-loading">טוען נתוני נרות...</p>}
          {state && state !== "loading" && <p className="state-msg chart-state-msg">{state}</p>}
          <div ref={containerRef} className="chart-container" />
        </div>
      </div>
    </div>
  );
}
