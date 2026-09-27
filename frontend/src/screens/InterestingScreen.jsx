import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import StockRow from "../components/StockRow";
import StockDetailModal from "../components/StockDetailModal";
import TechnicalPanel from "../components/TechnicalPanel";
import EmaBreakoutPanel from "../components/EmaBreakoutPanel";
import EmaTouch4hPanel from "../components/EmaTouch4hPanel";
import EmaDoubleTouchForexPanel from "../components/EmaDoubleTouchForexPanel";
import { getLatestScan, runScan, getCryptoScan, getCapScan } from "../api";
import { useLang } from "../context/LangContext";

const MARKETS = [
  { id: "stocks", key: "interesting.marketStocks" },
  { id: "crypto", key: "interesting.marketCrypto" },
  { id: "forex", key: "interesting.marketForex" },
];

const STOCK_CATEGORIES = [
  { id: "losers", key: "interesting.losers" },
  { id: "gainers", key: "interesting.gainers" },
  { id: "broken", key: "interesting.broken" },
  { id: "large", key: "interesting.large" },
  { id: "small", key: "interesting.small" },
  { id: "technical", key: "interesting.technical" },
];

const TECH_SUBTABS = [
  { id: "technical", key: "tech.subtabRsiEma" },
  { id: "ema-breakout", key: "tech.subtabBreakout" },
  { id: "ema-touch-4h", key: "tech.subtabTouch4h" },
];

const MoversList = forwardRef(function MoversList({ direction, onSelect, onBusyChange }, ref) {
  const { t } = useLang();
  const [scan, setScan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getLatestScan({ direction })
      .then((data) => setScan(data))
      .catch((err) => {
        setScan(null);
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, [direction]);

  const handleRescan = () => {
    setScanning(true);
    setError(null);
    runScan({ direction, sector: null, maxPrice: null })
      .then(setScan)
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  useImperativeHandle(ref, () => ({ rescan: handleRescan }));

  useEffect(() => {
    onBusyChange?.(scanning);
  }, [scanning, onBusyChange]);

  return (
    <div className="compact-list">
      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && error && <p className="disclaimer-note">{error}</p>}
      {!loading && !error && scan?.results?.length === 0 && <p className="disclaimer-note">{t("common.noResults")}</p>}
      {!loading &&
        scan?.results?.map((stock) => (
          <StockRow key={stock.id} stock={stock} direction={direction} onClick={() => onSelect(stock, direction)} />
        ))}
    </div>
  );
});

const MarketList = forwardRef(function MarketList({ fetchFn, onSelect, onBusyChange }, ref) {
  const { t } = useLang();
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = () => {
    setLoading(true);
    setError(null);
    fetchFn()
      .then((data) => setResults(data.results || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useImperativeHandle(ref, () => ({ rescan: load }));

  useEffect(() => {
    onBusyChange?.(loading);
  }, [loading, onBusyChange]);

  return (
    <div className="compact-list">
      {loading && <p className="state-msg">{t("common.loading")}</p>}
      {!loading && error && <p className="disclaimer-note">{error}</p>}
      {!loading && !error && results.length === 0 && <p className="disclaimer-note">{t("common.noResults")}</p>}
      {!loading &&
        results.map((stock) => {
          const direction = (stock.change_percent ?? 0) >= 0 ? "gainers" : "losers";
          return (
            <StockRow key={stock.symbol} stock={stock} direction={direction} onClick={() => onSelect(stock, direction)} />
          );
        })}
    </div>
  );
});

function TechnicalCategory() {
  const { t } = useLang();
  const [subTab, setSubTab] = useState("technical");
  return (
    <div className="compact-list">
      <div className="chip-row">
        {TECH_SUBTABS.map((item) => (
          <button
            key={item.id}
            className={`chip ${subTab === item.id ? "active" : ""}`}
            onClick={() => setSubTab(item.id)}
          >
            {t(item.key)}
          </button>
        ))}
      </div>
      {subTab === "technical" && <TechnicalPanel />}
      {subTab === "ema-breakout" && <EmaBreakoutPanel />}
      {subTab === "ema-touch-4h" && <EmaTouch4hPanel />}
    </div>
  );
}

export default function InterestingScreen() {
  const { t } = useLang();
  const [market, setMarket] = useState("stocks");
  const [category, setCategory] = useState("losers");
  const [selected, setSelected] = useState(null);
  const [marketOpen, setMarketOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const marketRef = useRef(null);
  const filterRef = useRef(null);
  const activeListRef = useRef(null);

  const handleSelect = (stock, direction) => setSelected({ stock, direction });
  const handleTopScan = () => activeListRef.current?.rescan();

  useEffect(() => {
    if (!marketOpen && !filterOpen) return;
    const handleClickOutside = (e) => {
      if (marketRef.current && !marketRef.current.contains(e.target)) setMarketOpen(false);
      if (filterRef.current && !filterRef.current.contains(e.target)) setFilterOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [marketOpen, filterOpen]);

  useEffect(() => {
    setScanBusy(false);
  }, [market, category]);

  const showTopScanButton = market === "stocks" ? category !== "technical" : market === "crypto";

  return (
    <div className="screen">
      <div className="scan-row">
        <div className="filter-menu" ref={marketRef}>
          <button
            className={`filter-toggle-btn ${marketOpen ? "active" : ""}`}
            onClick={() => setMarketOpen((v) => !v)}
            aria-label={t("interesting.selectMarket")}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7" rx="1.5" />
              <rect x="14" y="3" width="7" height="7" rx="1.5" />
              <rect x="14" y="14" width="7" height="7" rx="1.5" />
              <rect x="3" y="14" width="7" height="7" rx="1.5" />
            </svg>
          </button>
          {marketOpen && (
            <div className="filter-dropdown">
              {MARKETS.map((m) => (
                <button
                  key={m.id}
                  className={`filter-dropdown-item ${market === m.id ? "active" : ""}`}
                  onClick={() => {
                    setMarket(m.id);
                    setCategory("losers");
                    setMarketOpen(false);
                  }}
                >
                  {t(m.key)}
                </button>
              ))}
            </div>
          )}
        </div>

        {showTopScanButton && (
          <button className="btn btn-sm scan-now-btn" onClick={handleTopScan} disabled={scanBusy}>
            {scanBusy ? t("common.scanning") : t("common.rescanNow")}
          </button>
        )}

        {market === "stocks" && (
          <div className="filter-menu" ref={filterRef}>
            <button
              className={`filter-toggle-btn ${filterOpen ? "active" : ""}`}
              onClick={() => setFilterOpen((v) => !v)}
              aria-label={t("interesting.title")}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 5h18l-7 8v6l-4-2v-6Z" />
              </svg>
            </button>
            {filterOpen && (
              <div className="filter-dropdown filter-dropdown-start">
                {STOCK_CATEGORIES.map((c) => (
                  <button
                    key={c.id}
                    className={`filter-dropdown-item ${category === c.id ? "active" : ""}`}
                    onClick={() => {
                      setCategory(c.id);
                      setFilterOpen(false);
                    }}
                  >
                    {t(c.key)}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {market === "stocks" && category === "losers" && (
        <MoversList key="losers" ref={activeListRef} direction="losers" onSelect={handleSelect} onBusyChange={setScanBusy} />
      )}
      {market === "stocks" && category === "gainers" && (
        <MoversList key="gainers" ref={activeListRef} direction="gainers" onSelect={handleSelect} onBusyChange={setScanBusy} />
      )}
      {market === "stocks" && category === "broken" && (
        <MoversList key="broken" ref={activeListRef} direction="broken" onSelect={handleSelect} onBusyChange={setScanBusy} />
      )}
      {market === "stocks" && category === "large" && (
        <MarketList
          key="large"
          ref={activeListRef}
          fetchFn={() => getCapScan("large")}
          onSelect={handleSelect}
          onBusyChange={setScanBusy}
        />
      )}
      {market === "stocks" && category === "small" && (
        <MarketList
          key="small"
          ref={activeListRef}
          fetchFn={() => getCapScan("small")}
          onSelect={handleSelect}
          onBusyChange={setScanBusy}
        />
      )}
      {market === "stocks" && category === "technical" && <TechnicalCategory />}

      {market === "crypto" && (
        <MarketList key="crypto" ref={activeListRef} fetchFn={getCryptoScan} onSelect={handleSelect} onBusyChange={setScanBusy} />
      )}

      {market === "forex" && <EmaDoubleTouchForexPanel />}

      {selected && (
        <StockDetailModal stock={selected.stock} direction={selected.direction} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
