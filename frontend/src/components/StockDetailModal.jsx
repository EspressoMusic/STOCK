import { useEffect, useState } from "react";
import { formatPrice, formatPercent, formatCompact, formatRelative } from "../format";
import { useLang } from "../context/LangContext";
import { getNewsDigest } from "../api";
import { getHotSignal } from "../lib/hotSignal";
import StockChartModal from "./StockChartModal";

export default function StockDetailModal({ stock, direction, onClose }) {
  const { t, lang } = useLang();
  const [chartOpen, setChartOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [newsDigest, setNewsDigest] = useState(null);
  const [newsDigestLoading, setNewsDigestLoading] = useState(false);

  useEffect(() => {
    if (!advancedOpen || newsDigest !== null || newsDigestLoading) return;
    if (!stock.news || stock.news.length === 0) return;
    setNewsDigestLoading(true);
    getNewsDigest(stock.symbol, stock.news)
      .then((res) => setNewsDigest(res.summary))
      .catch(() => setNewsDigest(""))
      .finally(() => setNewsDigestLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [advancedOpen]);

  const newsDigestLines = (newsDigest || "")
    .split("\n")
    .map((line) => line.replace(/^[•\-*]\s*/, "").trim())
    .filter(Boolean);

  const isBroken = direction === "broken";
  const changeClass = isBroken ? "chg-broken" : direction === "losers" ? "chg-down" : "chg-up";
  const badgeValue = isBroken ? stock.fifty_two_week_change_percent : stock.change_percent;
  const hotSignal = getHotSignal(stock);

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal stock-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <button
            className="stock-detail-chart-icon-btn"
            onClick={() => setChartOpen(true)}
            aria-label={t("common.showChart")}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3v18h18" />
              <path d="M7 15l4-6 3 4 5-8" />
            </svg>
          </button>
          <div className="chart-modal-title">
            <span className="symbol">{stock.symbol}</span>
            {advancedOpen && stock.name && <span className="name">{stock.name}</span>}
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>

        <div className="stock-detail-body">
          <div className="stock-detail-top">
            <span className="stock-detail-price">{formatPrice(stock.price)}</span>
            <span className={`chg-badge ${changeClass}`}>{formatPercent(badgeValue)}</span>
          </div>

          {hotSignal.isHot && (
            <div className="hot-reason-badge">
              🔥{" "}
              {hotSignal.reason === "volume"
                ? `${t("stockDetail.hotVolumeReason")} ×${hotSignal.relativeVolume.toFixed(1)}`
                : `${t("stockDetail.hotNewsReason")} · ${formatRelative(hotSignal.newsItem.published_at, lang)}`}
            </div>
          )}

          {stock.company_blurb && (
            <div className="ai-bubble company-blurb-bubble">
              <span className="ai-label">{t("stockDetail.about")}</span>
              <p>{stock.company_blurb}</p>
            </div>
          )}

          {stock.ai_summary && (
            <div className="ai-bubble">
              <span className="ai-label">{t("stockDetail.sentiment")}</span>
              <p>{stock.ai_summary}</p>
            </div>
          )}

          {!advancedOpen && (
            <button
              className="stock-detail-advanced-toggle"
              onClick={() => setAdvancedOpen(true)}
            >
              {t("stockDetail.showAdvanced")}
            </button>
          )}

          {advancedOpen && (
            <div className="stock-detail-advanced">
              <div className="detail-grid">
                <div className="detail-item">
                  <span className="detail-label">{t("common.volume")}</span>
                  <span>{formatCompact(stock.volume)}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">{t("common.marketCap")}</span>
                  <span>{stock.market_cap ? `$${formatCompact(stock.market_cap)}` : "—"}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">{t("common.week52High")}</span>
                  <span>{formatPrice(stock.fifty_two_week_high)}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">{t("common.week52Low")}</span>
                  <span>{formatPrice(stock.fifty_two_week_low)}</span>
                </div>
              </div>

              <div className="analyst-block">
                <span className="detail-label">{t("common.analystTarget")}</span>
                {stock.target_mean_price ? (
                  <p>
                    {formatPrice(stock.target_mean_price)} ({formatPrice(stock.target_low_price)}–
                    {formatPrice(stock.target_high_price)}) · {stock.num_analyst_opinions || 0}
                    {stock.recommendation_key ? ` · ${stock.recommendation_key}` : ""}
                  </p>
                ) : (
                  <p className="muted">{t("common.noAnalystCoverage")}</p>
                )}
              </div>

              <div className="news-block">
                <span className="detail-label">{t("common.recentNews")}</span>
                {stock.news && stock.news.length > 0 ? (
                  newsDigestLoading ? (
                    <p className="muted">{t("common.summarizingNews")}</p>
                  ) : newsDigestLines.length > 0 ? (
                    <ul className="news-digest-list">
                      {newsDigestLines.map((line, i) => (
                        <li key={i}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">{t("common.noNews")}</p>
                  )
                ) : (
                  <p className="muted">{t("common.noNews")}</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {chartOpen && <StockChartModal symbol={stock.symbol} name={stock.name} onClose={() => setChartOpen(false)} />}
    </div>
  );
}
