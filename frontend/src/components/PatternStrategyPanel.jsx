import { useEffect, useState } from "react";
import { getLatestPatternStrategyScan, runPatternStrategyScan } from "../api";
import { formatPrice, formatDateTime } from "../format";
import { useLang } from "../context/LangContext";
import PatternDetailModal from "./PatternDetailModal";

export default function PatternStrategyPanel() {
  const { t, lang } = useLang();
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const handleScan = () => {
    setScanning(true);
    setError(null);
    runPatternStrategyScan()
      .then(setResult)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  useEffect(() => {
    setLoading(true);
    setError(null);
    getLatestPatternStrategyScan()
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => {
        setLoading(false);
        handleScan();
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalMatches = result ? result.stocks.length : 0;

  return (
    <div className="tech-panel">
      <div className="tech-panel-head">
        <button className="rescan-btn" onClick={handleScan} disabled={scanning}>
          {scanning ? t("tech.scanningPattern") : t("common.rescanNow")}
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
          {scanning && !result && <div className="warn">{t("tech.firstScanWarn")}</div>}
        </div>
      </div>

      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && error && <p className="state-msg">{error}</p>}
      {!loading && !error && result && totalMatches === 0 && <p className="state-msg">{t("tech.noMatchesPattern")}</p>}

      {result && totalMatches > 0 && (
        <div className="compact-list">
          {result.stocks.map((c, i) => (
            <button key={`${c.symbol}-${c.direction}-${i}`} className="stock-row" onClick={() => setSelected(c)}>
              <span className="stock-row-symbol">{c.symbol}</span>
              <span className={`chg-badge ${c.direction === "long" ? "chg-up" : "chg-down"}`}>
                {t(c.direction === "long" ? "tech.directionLong" : "tech.directionShort")}
              </span>
              <span className="stock-row-price">{formatPrice(c.price)}</span>
            </button>
          ))}
        </div>
      )}

      {selected && <PatternDetailModal candidate={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
