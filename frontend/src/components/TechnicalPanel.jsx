import { useEffect, useState } from "react";
import { getLatestTechnicalScan, runTechnicalScan } from "../api";
import { formatPrice, formatRate, formatDateTime } from "../format";
import { useLang } from "../context/LangContext";

function CandidateRow({ c, t }) {
  const fmt = c.asset_class === "forex" ? formatRate : formatPrice;
  const signalLabel = c.signal === "overbought" ? t("tech.overbought") : c.signal === "oversold" ? t("tech.oversold") : c.signal;
  return (
    <div className="tech-row">
      <div className="tech-row-main">
        <span className="symbol">{c.symbol}</span>
        <span className={`signal-badge signal-${c.signal}`}>{signalLabel}</span>
      </div>
      <div className="name">{c.name || "—"}</div>
      <div className="tech-row-stats">
        <span>
          {c.asset_class === "forex" ? t("tech.rate") : t("tech.price")} {fmt(c.price)}
        </span>
        <span>Stoch %K {c.stoch_k.toFixed(1)}</span>
        <span>Stoch %D {c.stoch_d.toFixed(1)}</span>
        <span>
          EMA{c.ema_period} {fmt(c.ema)}
        </span>
      </div>
    </div>
  );
}

export default function TechnicalPanel() {
  const { t, lang } = useLang();
  const [timeframe, setTimeframe] = useState("15m"); // "5m" | "15m"
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getLatestTechnicalScan(timeframe)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false));
  }, [timeframe]);

  const handleScan = () => {
    setScanning(true);
    setError(null);
    runTechnicalScan(timeframe)
      .then(setResult)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  const totalMatches = result ? result.stocks.length + result.forex.length : 0;

  return (
    <div className="tech-panel">
      <div className="tech-panel-head">
        <div className="timeframe-toggle">
          <button className={timeframe === "5m" ? "active" : ""} onClick={() => setTimeframe("5m")} disabled={scanning}>
            {t("tech.candles5m")}
          </button>
          <button className={timeframe === "15m" ? "active" : ""} onClick={() => setTimeframe("15m")} disabled={scanning}>
            {t("tech.candles15m")}
          </button>
        </div>
        <button className="rescan-btn" onClick={handleScan} disabled={scanning}>
          {scanning ? (timeframe === "5m" ? t("tech.scanning5m") : t("tech.scanning15m")) : t("common.rescanNow")}
        </button>
        <div className="status-box">
          {result && (
            <div>
              {t("tech.scanned")}: {formatDateTime(result.scanned_at, lang)}
            </div>
          )}
          {result && (
            <div>
              {totalMatches} {t("tech.matchesOf")} {result.universe_stock_count} {t("tech.stocks")} + {result.universe_forex_count}{" "}
              {t("tech.forexPairs")}
            </div>
          )}
          {scanning && <div className="warn">{t("tech.firstScanWarn")}</div>}
        </div>
      </div>

      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && error && <p className="state-msg">{error}</p>}
      {!loading && !error && !result && <p className="state-msg">{t("tech.noScanYet")}</p>}
      {!loading && !error && result && totalMatches === 0 && <p className="state-msg">{t("tech.noMatchesRsiEma")}</p>}

      {result && totalMatches > 0 && (
        <div className="tech-groups">
          {result.stocks.length > 0 && (
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
          )}
          {result.forex.length > 0 && (
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
          )}
        </div>
      )}
    </div>
  );
}
