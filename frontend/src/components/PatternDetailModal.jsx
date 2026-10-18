import { formatPrice, formatDateTime } from "../format";
import { useLang } from "../context/LangContext";
import { useNav } from "../context/NavContext";

export default function PatternDetailModal({ candidate, onClose }) {
  const { t, lang } = useLang();
  const { goTo } = useNav();
  const isLong = candidate.direction === "long";

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal stock-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <button
            className="stock-detail-chart-icon-btn"
            onClick={() => {
              goTo("chart-scan", { symbol: candidate.symbol });
              onClose();
            }}
            aria-label={t("common.showChart")}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3v18h18" />
              <path d="M7 15l4-6 3 4 5-8" />
            </svg>
          </button>
          <div className="chart-modal-title">
            <span className="symbol">{candidate.symbol}</span>
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
          {candidate.name && (
            <span className="name stock-detail-name" dir="auto">
              {candidate.name}
            </span>
          )}
        </div>

        <div className="stock-detail-body">
          <div className="stock-detail-top">
            <span className="stock-detail-price">{formatPrice(candidate.price)}</span>
            <span className={`chg-badge ${isLong ? "chg-up" : "chg-down"}`}>
              {t(isLong ? "tech.directionLong" : "tech.directionShort")}
            </span>
            <span className="signal-badge signal-breakout">
              {candidate.touch_count} {t("tech.aoiTouches")}
            </span>
          </div>

          <div className="detail-grid">
            <div className="detail-item">
              <span className="detail-label">{t("tech.aoiZone")}</span>
              <span>{formatPrice(candidate.zone_low)} – {formatPrice(candidate.zone_high)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("tech.ema")}</span>
              <span>{formatPrice(candidate.ema)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("tech.lastTouchAt")}</span>
              <span>{formatDateTime(candidate.last_touch_time, lang)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("tech.candleAt")}</span>
              <span>{formatDateTime(candidate.candle_time, lang)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
