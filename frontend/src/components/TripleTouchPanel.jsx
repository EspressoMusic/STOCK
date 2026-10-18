import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { getLatestTripleTouchScan, runTripleTouchScan } from "../api";
import { formatPrice } from "../format";
import { useLang } from "../context/LangContext";
import StockDetailModal from "./StockDetailModal";

function formatLevel(value, market) {
  if (market === "forex") return value.toFixed(value >= 20 ? 3 : 5);
  if (value < 1) return `$${value.toPrecision(4)}`;
  return formatPrice(value);
}

const TripleTouchPanel = forwardRef(function TripleTouchPanel({ market, onBusyChange, onScannedAt }, ref) {
  const { t } = useLang();
  const [result, setResult] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const handleScan = () => {
    setScanning(true);
    setError(null);
    runTripleTouchScan(market)
      .then(setResult)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  useImperativeHandle(ref, () => ({ rescan: handleScan }));

  // Show the last cached scan right away; with nothing cached yet, start one so the
  // tab isn't empty on first open.
  useEffect(() => {
    setLoading(true);
    setError(null);
    getLatestTripleTouchScan(market)
      .then(setResult)
      .catch(() => {
        setResult(null);
        handleScan();
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  useEffect(() => {
    onBusyChange?.(scanning);
  }, [scanning, onBusyChange]);

  useEffect(() => {
    onScannedAt?.(result?.scanned_at ?? null);
  }, [result, onScannedAt]);

  const results = result?.results ?? [];

  return (
    <div className="compact-list">
      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && scanning && !result && <p className="state-msg">{t("tech.firstScanWarn")}</p>}
      {!loading && error && <p className="disclaimer-note">{error}</p>}
      {!loading && !error && result && results.length === 0 && (
        <p className="disclaimer-note">{t("tech.noMatchesTripleTouch")}</p>
      )}
      {!loading &&
        results.map((c) => {
          const isLong = c.direction === "long";
          return (
            <button
              key={c.symbol}
              className={`stock-row${c.at_level ? " stock-row-hot" : ""}`}
              onClick={() => setSelected(c)}
            >
              <span className="stock-row-symbol">{c.symbol}</span>
              <span className={`chg-badge ${isLong ? "chg-up" : "chg-down"}`}>
                {t(isLong ? "tech.directionLong" : "tech.directionShort")} ×{c.touch_count}
              </span>
              <span className="stock-row-price">{formatLevel(c.price, market)}</span>
            </button>
          );
        })}

      {selected && (
        <StockDetailModal
          key={selected.symbol}
          stock={{ symbol: selected.symbol, name: selected.name, price: selected.price }}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
});

export default TripleTouchPanel;
