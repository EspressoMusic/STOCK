import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { getDailyDigest } from "../api";
import { useLang } from "../context/LangContext";
import { formatPrice, formatPnl, formatPercent, formatDateTime } from "../format";

// Combines the AI daily digest with a full stats grid and a P&L progress
// chart, so "the portfolio" is one place instead of a separate digest button.
export default function PortfolioModal({
  cash,
  holdings,
  quotes,
  transactions,
  totalValue,
  totalPnl,
  dayChangePercent,
  onClose,
}) {
  const { t, lang } = useLang();
  const [summary, setSummary] = useState(null);
  const [generatedAt, setGeneratedAt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const chartContainerRef = useRef(null);

  useEffect(() => {
    setLoading(true);
    setError(false);
    getDailyDigest({
      cash,
      holdings: holdings.map((h) => ({
        symbol: h.symbol,
        shares: h.shares,
        avgPrice: h.avgPrice,
        price: quotes[h.symbol]?.price ?? null,
        changePercent: quotes[h.symbol]?.change_percent ?? null,
      })),
    })
      .then((res) => {
        setSummary(res.summary);
        setGeneratedAt(res.generated_at);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Realized P&L up to now for each closed trade, plus a final point that
  // folds in the unrealized P&L on whatever is still open. There's no
  // historical price data behind the demo portfolio, so this tracks trading
  // results over time rather than a true historical account value.
  const progressPoints = useMemo(() => {
    const closed = [...transactions]
      .filter((tx) => tx.pnl != null)
      .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    if (closed.length === 0) return [];
    let cumulative = 0;
    const seen = new Set();
    const points = [];
    for (const tx of closed) {
      cumulative += tx.pnl;
      let time = Math.floor(new Date(tx.timestamp).getTime() / 1000);
      while (seen.has(time)) time += 1;
      seen.add(time);
      points.push({ time, value: cumulative });
    }
    let nowTime = Math.floor(Date.now() / 1000);
    while (seen.has(nowTime)) nowTime += 1;
    points.push({ time: nowTime, value: cumulative + totalPnl });
    return points;
  }, [transactions, totalPnl]);

  useEffect(() => {
    if (!chartContainerRef.current || progressPoints.length < 2) return;
    const style = getComputedStyle(document.documentElement);
    const finalValue = progressPoints[progressPoints.length - 1].value;
    const chart = createChart(chartContainerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: style.getPropertyValue("--text").trim(), attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      handleScroll: false,
      handleScale: false,
    });
    const series = chart.addSeries(LineSeries, {
      color: finalValue >= 0 ? style.getPropertyValue("--up").trim() : style.getPropertyValue("--down").trim(),
      lineWidth: 3,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    series.setData(progressPoints);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [progressPoints]);

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal portfolio-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="symbol">{t("portfolio.myPortfolio")}</span>
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>

        <div className="chart-modal-body portfolio-modal-body">
          {loading && <p className="muted">{t("dailyDigest.loading")}</p>}
          {!loading && error && <p className="muted">{t("dailyDigest.error")}</p>}
          {!loading && !error && summary && (
            <div className="ai-bubble">
              <p>{summary}</p>
            </div>
          )}

          <div className="detail-grid">
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.value")}</span>
              <span>{formatPrice(totalValue)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.cashAvailable")}</span>
              <span>{formatPrice(cash)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.totalPnl")}</span>
              <span style={{ color: totalPnl >= 0 ? "var(--up)" : "var(--down)" }}>
                {totalPnl >= 0 ? "+" : ""}
                {formatPnl(totalPnl)}
              </span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.changePercent")}</span>
              <span style={{ color: dayChangePercent >= 0 ? "var(--up)" : "var(--down)" }}>
                {formatPercent(dayChangePercent)}
              </span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.openPositions")}</span>
              <span>{holdings.length}</span>
            </div>
          </div>

          <div className="portfolio-progress-section">
            <span className="detail-label">{t("portfolio.progressChart")}</span>
            {progressPoints.length >= 2 ? (
              <div className="portfolio-progress-chart" ref={chartContainerRef} />
            ) : (
              <p className="muted">{t("portfolio.noHistoryYet")}</p>
            )}
          </div>

          {generatedAt && !loading && !error && (
            <p className="muted daily-digest-timestamp">{formatDateTime(generatedAt, lang)}</p>
          )}
        </div>
      </div>
    </div>
  );
}
