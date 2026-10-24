import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { getLatestStochExtremeScan, runStochExtremeScan } from "../api";
import { formatPrice } from "../format";
import { useLang } from "../context/LangContext";
import StockDetailModal from "./StockDetailModal";

function formatStockPrice(value) {
  if (value < 1) return `$${value.toPrecision(4)}`;
  return formatPrice(value);
}

// "Shilo 2": stocks whose Stoch RSI is overbought on both the daily and the 4h chart,
// or oversold on both.
const StochExtremePanel = forwardRef(function StochExtremePanel({ onBusyChange, onScannedAt }, ref) {
  const { t } = useLang();
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const handleScan = () => {
    setScanning(true);
    setError(null);
    runStochExtremeScan()
      .then(setResult)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  useImperativeHandle(ref, () => ({ rescan: handleScan }));

  // Show the last cached scan right away; with nothing cached yet, start one so the
  // tab isn't empty on first open.
  useEffect(() => {
    getLatestStochExtremeScan()
      .then(setResult)
      .catch(() => {
        setResult(null);
        handleScan();
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    onBusyChange?.(scanning);
  }, [scanning, onBusyChange]);

  useEffect(() => {
    onScannedAt?.(result?.scanned_at ?? null);
  }, [result, onScannedAt]);

  const stocks = result?.stocks ?? [];

  return (
    <div className="compact-list">
      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && scanning && !result && <p className="state-msg">{t("tech.firstScanWarn")}</p>}
      {!loading && error && <p className="disclaimer-note">{error}</p>}
      {!loading && !error && result && stocks.length === 0 && (
        <p className="disclaimer-note">{t("tech.noMatchesStochExtreme")}</p>
      )}
      {!loading &&
        stocks.map((c) => (
          <button key={c.symbol} className="stock-row" onClick={() => setSelected(c)}>
            <span className="stock-row-symbol">{c.symbol}</span>
            <span className={`chg-badge chg-${c.signal}`}>
              {t(c.signal === "overbought" ? "tech.overbought" : "tech.oversold")}
            </span>
            <span className="stock-row-price">{formatStockPrice(c.price)}</span>
          </button>
        ))}

      {selected && (
        <StockDetailModal
          key={selected.symbol}
          stock={{ symbol: selected.symbol, name: selected.name, price: selected.price }}
          chartParams={{ timeframe: "4h" }}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
});

export default StochExtremePanel;
