import { useEffect, useState } from "react";
import { useNav } from "../context/NavContext";
import { useLang } from "../context/LangContext";
import useWatchlist from "../hooks/useWatchlist";
import StockRow from "../components/StockRow";
import StockDetailModal from "../components/StockDetailModal";
import { getQuote, getStockProfile } from "../api";

const QUOTES_REFRESH_INTERVAL_MS = 30000;

export default function WatchlistScreen() {
  const { goTo } = useNav();
  const { t } = useLang();
  const { items } = useWatchlist();
  // symbol -> latest quote, so each row can show price and daily change like the scanner rows.
  const [quotes, setQuotes] = useState({});
  // symbol -> AI summary, blurb, news and analyst fields for the detail modal, fetched on first open.
  const [profiles, setProfiles] = useState({});
  const [selected, setSelected] = useState(null);

  const symbolsKey = items.map((s) => s.symbol).join(",");

  useEffect(() => {
    if (!symbolsKey) return;
    let cancelled = false;
    const symbols = symbolsKey.split(",");
    const refresh = () => {
      symbols.forEach((symbol) => {
        getQuote(symbol)
          .then((q) => {
            if (!cancelled) setQuotes((prev) => ({ ...prev, [symbol]: q }));
          })
          .catch(() => {});
      });
    };
    refresh();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, QUOTES_REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [symbolsKey]);

  const openDetail = (item) => {
    setSelected(item);
    if (profiles[item.symbol]) return;
    getStockProfile(item.symbol)
      .then((p) => setProfiles((prev) => ({ ...prev, [item.symbol]: p })))
      .catch(() => {});
  };

  const selectedQuote = selected && quotes[selected.symbol];
  const selectedStock = selected && {
    symbol: selected.symbol,
    name: selected.name || selectedQuote?.name,
    price: selectedQuote?.price,
    change_percent: selectedQuote?.change_percent,
    ...profiles[selected.symbol],
  };

  return (
    <div className="screen watchlist-screen">
      <section className="watchlist-box">
        <h2 className="watchlist-box-title">{t("watchlist.title")}</h2>
        {items.length === 0 ? (
          <p className="watchlist-box-empty">{t("watchlist.empty")}</p>
        ) : (
          <div className="compact-list">
            {items.map((s) => {
              const q = quotes[s.symbol];
              const stock = { symbol: s.symbol, price: q?.price, change_percent: q?.change_percent };
              const direction = (q?.change_percent ?? 0) < 0 ? "losers" : "gainers";
              return <StockRow key={s.symbol} stock={stock} direction={direction} onClick={() => openDetail(s)} />;
            })}
          </div>
        )}
      </section>

      <button type="button" className="btn btn-primary watchlist-scanner-btn" onClick={() => goTo("interesting")}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-4-4" />
        </svg>
        {t("watchlist.goToScanner")}
      </button>

      {selectedStock && (
        <StockDetailModal
          key={selectedStock.symbol}
          stock={selectedStock}
          direction={(selectedStock.change_percent ?? 0) < 0 ? "losers" : "gainers"}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
