import { useEffect, useState } from "react";
import { getLatestEmaTouch4hScan, runEmaTouch4hScan } from "../api";
import { formatPrice, formatDateTime } from "../format";
import { useLang } from "../context/LangContext";

function CandidateRow({ c, t, lang }) {
  return (
    <div className="tech-row">
      <div className="tech-row-main">
        <span className="symbol">{c.symbol}</span>
        <span className="signal-badge signal-touch">
          {t("tech.emaTouchBadge")} {c.ema_period}
        </span>
      </div>
      <div className="name">{c.name || "—"}</div>
      <div className="tech-row-stats">
        <span>
          {t("tech.price")} {formatPrice(c.price)}
        </span>
        <span>
          EMA{c.ema_period} {formatPrice(c.ema)}
        </span>
        <span>
          {t("tech.candleAt")}: {formatDateTime(c.candle_time, lang)}
        </span>
      </div>
    </div>
  );
}

export default function EmaTouch4hPanel() {
  const { t, lang } = useLang();
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getLatestEmaTouch4hScan()
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false));
  }, []);

  const handleScan = () => {
    setScanning(true);
    setError(null);
    runEmaTouch4hScan()
      .then(setResult)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  const totalMatches = result ? result.stocks.length : 0;

  return (
    <div className="tech-panel">
      <div className="tech-panel-head">
        <button className="rescan-btn" onClick={handleScan} disabled={scanning}>
          {scanning ? t("tech.scanning4h") : t("common.rescanNow")}
        </button>
        <div className="status-box">
          {result && (
            <div>
              {t("tech.scanned")}: {formatDateTime(result.scanned_at, lang)}
            </div>
          )}
          {result && (
            <div>
              {totalMatches} {t("tech.matchesOf")} {result.universe_stock_count} {t("tech.stocks")}
            </div>
          )}
          {scanning && <div className="warn">{t("tech.firstScanWarn")}</div>}
        </div>
      </div>

      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && error && <p className="state-msg">{error}</p>}
      {!loading && !error && !result && <p className="state-msg">{t("tech.noScanYet")}</p>}
      {!loading && !error && result && totalMatches === 0 && <p className="state-msg">{t("tech.noMatchesTouch4h")}</p>}

      {result && totalMatches > 0 && (
        <div className="tech-groups">
          <section>
            <h3 className="tech-group-title">
              {t("tech.stocks")} ({result.stocks.length})
            </h3>
            <div className="tech-list">
              {result.stocks.map((c) => (
                <CandidateRow key={c.symbol} c={c} t={t} lang={lang} />
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
