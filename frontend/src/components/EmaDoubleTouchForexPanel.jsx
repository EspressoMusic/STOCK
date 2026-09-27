import { useEffect, useState } from "react";
import { getLatestEmaDoubleTouchForexScan, runEmaDoubleTouchForexScan } from "../api";
import { formatRate, formatDateTime } from "../format";
import { useLang } from "../context/LangContext";
import StockChartModal from "./StockChartModal";

function CandidateRow({ c, t, lang }) {
  const [chartOpen, setChartOpen] = useState(false);

  return (
    <div className="tech-row">
      <div className="tech-row-main">
        <span className="symbol">{c.symbol}</span>
        <span className="signal-badge signal-double-touch">
          {t("tech.emaDoubleTouchBadge")} {c.ema_period}
        </span>
      </div>
      <div className="name">{c.name || "—"}</div>
      <div className="tech-row-stats">
        <span>
          {t("tech.rate")} {formatRate(c.price)}
        </span>
        <span>
          EMA{c.ema_period} {formatRate(c.ema)}
        </span>
        <span>
          {t("tech.firstTouch")}: {formatDateTime(c.first_touch_time, lang)}
        </span>
        <span>
          {t("tech.secondTouch")}: {formatDateTime(c.second_touch_time, lang)}
        </span>
      </div>
      <div className="tech-row-actions">
        <button className="chart-btn" onClick={() => setChartOpen(true)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" />
            <path d="M7 15l4-6 3 4 5-8" />
          </svg>
          {t("common.showChart")}
        </button>
      </div>

      {chartOpen && <StockChartModal symbol={c.symbol} name={c.name} onClose={() => setChartOpen(false)} />}
    </div>
  );
}

export default function EmaDoubleTouchForexPanel() {
  const { t, lang } = useLang();
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getLatestEmaDoubleTouchForexScan()
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false));
  }, []);

  const handleScan = () => {
    setScanning(true);
    setError(null);
    runEmaDoubleTouchForexScan()
      .then(setResult)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  const totalMatches = result ? result.forex.length : 0;

  return (
    <div className="tech-panel">
      <div className="tech-panel-head">
        <button className="rescan-btn" onClick={handleScan} disabled={scanning}>
          {scanning ? t("tech.scanningHourly") : t("common.rescanNow")}
        </button>
        <div className="status-box">
          {result && (
            <div>
              {t("tech.scanned")}: {formatDateTime(result.scanned_at, lang)}
            </div>
          )}
          {result && (
            <div>
              {totalMatches} {t("tech.matchesOf")} {result.universe_forex_count} {t("tech.forexPairs")}
            </div>
          )}
          {scanning && <div className="warn">{t("tech.firstScanWarn")}</div>}
        </div>
      </div>

      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && error && <p className="state-msg">{error}</p>}
      {!loading && !error && !result && <p className="state-msg">{t("tech.noScanYet")}</p>}
      {!loading && !error && result && totalMatches === 0 && <p className="state-msg">{t("tech.noMatchesDoubleTouch")}</p>}

      {result && totalMatches > 0 && (
        <div className="tech-groups">
          <section>
            <h3 className="tech-group-title">
              {t("tech.forexPairs")} ({result.forex.length})
            </h3>
            <div className="tech-list">
              {result.forex.map((c) => (
                <CandidateRow key={c.symbol} c={c} t={t} lang={lang} />
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
