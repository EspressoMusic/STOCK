import { useEffect, useState } from "react";
import { formatPrice, formatPercent, formatRelative } from "../format";
import { useLang } from "../context/LangContext";
import { useNav } from "../context/NavContext";
import { getNewsDigest, getStockProfile } from "../api";
import { getHotSignal } from "../lib/hotSignal";
import useWatchlist from "../hooks/useWatchlist";

const FRESH_NEWS_PHRASE = "כותרות חדשות טריות";

function renderSentimentText(text, onNewsPhraseClick) {
  const idx = text.indexOf(FRESH_NEWS_PHRASE);
  if (idx === -1 || !onNewsPhraseClick) return text;
  const before = text.slice(0, idx);
  const after = text.slice(idx + FRESH_NEWS_PHRASE.length);
  return (
    <>
      {before}
      <strong
        className="ai-summary-news-link"
        onClick={onNewsPhraseClick}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onNewsPhraseClick();
          }
        }}
      >
        {FRESH_NEWS_PHRASE}
      </strong>
      {after}
    </>
  );
}

function withoutNulls(obj) {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v != null));
}

export default function StockDetailModal({ stock: baseStock, direction: baseDirection, onClose }) {
  const { t, lang } = useLang();
  const { goTo } = useNav();
  const { isSaved, toggle } = useWatchlist();
  const saved = isSaved(baseStock.symbol);

  // Lists other than the movers scan (triple-touch, cap/crypto) don't carry the AI
  // explanation, so fetch it on open and layer it over what the list already had.
  const needsProfile = baseStock.company_blurb == null && baseStock.ai_summary == null;
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(needsProfile);

  useEffect(() => {
    if (!needsProfile) return;
    let cancelled = false;
    getStockProfile(baseStock.symbol)
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setProfileLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [baseStock.symbol, needsProfile]);

  const stock = profile ? { ...baseStock, ...withoutNulls(profile) } : baseStock;
  const direction = baseDirection ?? ((stock.change_percent ?? 0) < 0 ? "losers" : "gainers");

  const [newsModalOpen, setNewsModalOpen] = useState(false);
  const [newsDigest, setNewsDigest] = useState(null);
  const [newsDigestLoading, setNewsDigestLoading] = useState(false);

  const hasNews = !!(stock.news && stock.news.length > 0);

  useEffect(() => {
    if (!newsModalOpen || newsDigest !== null || newsDigestLoading) return;
    if (!hasNews) return;
    setNewsDigestLoading(true);
    getNewsDigest(stock.symbol, stock.news)
      .then((res) => setNewsDigest(res.summary))
      .catch(() => setNewsDigest(""))
      .finally(() => setNewsDigestLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newsModalOpen]);

  const newsDigestLines = (newsDigest || "")
    .split("\n")
    .map((line) => line.replace(/^[•\-*]\s*/, "").trim())
    .filter(Boolean);

  const isBroken = direction === "broken";
  const changeClass = isBroken ? "chg-broken" : direction === "losers" ? "chg-down" : "chg-up";
  const badgeValue = isBroken ? stock.fifty_two_week_change_percent : stock.change_percent;
  const hotSignal = getHotSignal(stock);

  return (
    <>
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal stock-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="symbol">{stock.symbol}</span>
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

          {needsProfile && profileLoading && <p className="muted">{t("stockDetail.loadingProfile")}</p>}

          {stock.company_blurb && (
            <div className="ai-bubble company-blurb-bubble">
              <span className="ai-label">{t("stockDetail.about")}</span>
              <p>{stock.company_blurb}</p>
            </div>
          )}

          {stock.ai_summary && (
            <div className="ai-bubble">
              <span className="ai-label">{t("stockDetail.sentiment")}</span>
              <p>
                {renderSentimentText(
                  stock.ai_summary,
                  hasNews ? () => setNewsModalOpen(true) : null
                )}
              </p>
            </div>
          )}

          <div className="stock-detail-actions">
            <button
              className="stock-detail-chart-icon-btn"
              onClick={() => {
                goTo("chart-scan", { symbol: stock.symbol });
                onClose();
              }}
              aria-label={t("common.showChart")}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 3v18h18" />
                <path d="M7 15l4-6 3 4 5-8" />
              </svg>
            </button>
            <button
              className={`stock-detail-chart-icon-btn watch-star-btn${saved ? " is-saved" : ""}`}
              onClick={() => toggle(stock.symbol, stock.name)}
              aria-pressed={saved}
              aria-label={saved ? t("watchlist.remove") : t("watchlist.save")}
              title={saved ? t("watchlist.remove") : t("watchlist.save")}
            >
              <svg viewBox="0 0 24 24" fill={saved ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    {newsModalOpen && (
      <div className="chart-modal-overlay" onClick={() => setNewsModalOpen(false)}>
        <div className="chart-modal news-summary-modal" onClick={(e) => e.stopPropagation()}>
          <div className="chart-modal-head">
            <div className="chart-modal-title">
              <span className="symbol">{t("common.recentNews")}</span>
            </div>
            <button
              className="chart-modal-close"
              onClick={() => setNewsModalOpen(false)}
              aria-label={t("common.close")}
            >
              ✕
            </button>
          </div>
          <div className="chart-modal-body">
            {newsDigestLoading ? (
              <p className="muted">{t("common.summarizingNews")}</p>
            ) : newsDigestLines.length > 0 ? (
              <ul className="news-digest-list">
                {newsDigestLines.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">{t("common.noNews")}</p>
            )}
          </div>
        </div>
      </div>
    )}
    </>
  );
}
