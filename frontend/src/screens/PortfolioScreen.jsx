import { useEffect, useMemo, useState } from "react";
import usePortfolio from "../hooks/usePortfolio";
import usePortfolioDisplay from "../hooks/usePortfolioDisplay";
import { getQuote } from "../api";
import { formatPrice, formatPercent } from "../format";
import { useLang } from "../context/LangContext";
import WorldNewsPanel from "../components/WorldNewsPanel";

export default function PortfolioScreen() {
  const { cash, holdings, sell } = usePortfolio();
  const { t } = useLang();
  const [quotes, setQuotes] = useState({});
  const [loading, setLoading] = useState(false);
  const { isAdvanced } = usePortfolioDisplay();

  const refreshQuotes = useMemo(
    () => () => {
      if (holdings.length === 0) {
        setQuotes({});
        return;
      }
      setLoading(true);
      Promise.all(
        holdings.map((h) =>
          getQuote(h.symbol)
            .then((q) => [h.symbol, q])
            .catch(() => [h.symbol, null])
        )
      )
        .then((pairs) => setQuotes(Object.fromEntries(pairs)))
        .finally(() => setLoading(false));
    },
    [holdings]
  );

  useEffect(() => {
    refreshQuotes();
  }, [refreshQuotes]);

  const { totalValue, dayChangePercent, totalPnl, totalCost } = useMemo(() => {
    let holdingsValue = 0;
    let weightedChange = 0;
    let cost = 0;
    for (const h of holdings) {
      const q = quotes[h.symbol];
      const price = q?.price ?? h.avgPrice;
      const value = price * h.shares;
      holdingsValue += value;
      cost += h.avgPrice * h.shares;
      if (q?.change_percent != null) weightedChange += value * q.change_percent;
    }
    const total = cash + holdingsValue;
    return {
      totalValue: total,
      dayChangePercent: holdingsValue > 0 ? weightedChange / holdingsValue : 0,
      totalPnl: holdingsValue - cost,
      totalCost: cost,
    };
  }, [cash, holdings, quotes]);

  const isUp = dayChangePercent >= 0;

  return (
    <div className="screen">
      <div className="portfolio-hero">
        {isAdvanced && <div className="portfolio-hero-label">{t("portfolio.value")}</div>}
        <div className="portfolio-hero-value">{formatPrice(totalValue)}</div>
        {isAdvanced && (
          <div className="portfolio-hero-badges">
            <span className={`badge-pill ${isUp ? "bullish" : "bearish"}`}>{formatPercent(dayChangePercent)}</span>
            {totalCost > 0 && (
              <span className={`badge-pill ${totalPnl >= 0 ? "bullish" : "bearish"}`}>
                {totalPnl >= 0 ? "+" : ""}
                {formatPrice(totalPnl)}
              </span>
            )}
          </div>
        )}
        {isAdvanced && (
          <div className="portfolio-hero-meta">
            {t("portfolio.cashAvailable")} {formatPrice(cash)}
          </div>
        )}
      </div>

      {holdings.length > 0 && (
        <div className="holdings-list">
          {holdings.map((h) => {
            const q = quotes[h.symbol];
            const price = q?.price ?? h.avgPrice;
            const pnlPercent = ((price - h.avgPrice) / h.avgPrice) * 100;
            return (
              <div className="holding-row" key={h.symbol}>
                <div className="holding-main">
                  <div className="holding-symbol">{h.symbol}</div>
                  <div className="holding-meta">
                    {h.shares} {t("portfolio.units")} · {formatPrice(h.avgPrice)}
                  </div>
                </div>
                <div className="holding-side">
                  <div className="holding-price">{formatPrice(price)}</div>
                  <div className="holding-meta" style={{ color: pnlPercent >= 0 ? "var(--up)" : "var(--down)" }}>
                    {formatPercent(pnlPercent)}
                  </div>
                </div>
                <button className="holding-remove" title={t("portfolio.sellAll")} onClick={() => sell(h.symbol, h.shares, price)}>
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}

      {holdings.length === 0 && isAdvanced && <p className="disclaimer-note">{t("portfolio.empty")}</p>}

      {holdings.length > 0 && (
        <button className="btn btn-ghost btn-sm" onClick={refreshQuotes} disabled={loading}>
          {loading ? t("common.loading") : t("common.refresh")}
        </button>
      )}

      <WorldNewsPanel />
    </div>
  );
}
