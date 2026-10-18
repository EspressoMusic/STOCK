import { useEffect, useMemo, useState } from "react";
import usePortfolio from "../hooks/usePortfolio";
import usePortfolioDisplay from "../hooks/usePortfolioDisplay";
import { getQuote } from "../api";
import { formatPrice, formatPnl, formatPercent, formatDateTime } from "../format";
import { useLang } from "../context/LangContext";
import PortfolioModal from "../components/PortfolioModal";

export default function PortfolioScreen({ skin }) {
  const { cash, holdings, transactions, closePosition, setStopLossTakeProfit } = usePortfolio();
  const { t, lang } = useLang();
  const [quotes, setQuotes] = useState({});
  const [selectedTx, setSelectedTx] = useState(null);
  const [selectedHolding, setSelectedHolding] = useState(null);
  const [showAllHistory, setShowAllHistory] = useState(false);
  const [mt5Tab, setMt5Tab] = useState("positions");
  const [portfolioModalOpen, setPortfolioModalOpen] = useState(false);
  const [slInput, setSlInput] = useState("");
  const [tpInput, setTpInput] = useState("");
  const [slTpError, setSlTpError] = useState(null);
  const [autoClosedNotices, setAutoClosedNotices] = useState([]);
  const { isAdvanced } = usePortfolioDisplay();
  const isMt5 = skin === "mt5";

  const refreshQuotes = useMemo(
    () => () => {
      if (holdings.length === 0) {
        setQuotes({});
        return;
      }
      Promise.all(
        holdings.map((h) =>
          getQuote(h.symbol)
            .then((q) => [h.symbol, q])
            .catch(() => [h.symbol, null])
        )
      ).then((pairs) => setQuotes(Object.fromEntries(pairs)));
    },
    [holdings]
  );

  const QUOTES_REFRESH_INTERVAL_MS = 15000;

  useEffect(() => {
    refreshQuotes();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refreshQuotes();
    }, QUOTES_REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refreshQuotes]);

  // Stop-loss/take-profit auto-close: checked against every fresh quote while
  // this screen is mounted (there's no backend account behind the demo
  // portfolio to watch it in the background — see usePortfolio.js).
  useEffect(() => {
    const triggered = [];
    for (const h of holdings) {
      if (h.stopLoss == null && h.takeProfit == null) continue;
      const q = quotes[h.symbol];
      if (!q || q.price == null) continue;
      const isShort = h.shares < 0;
      const hitStop = h.stopLoss != null && (isShort ? q.price >= h.stopLoss : q.price <= h.stopLoss);
      const hitTarget = h.takeProfit != null && (isShort ? q.price <= h.takeProfit : q.price >= h.takeProfit);
      if (hitStop || hitTarget) {
        triggered.push({ symbol: h.symbol, price: q.price, kind: hitStop ? "stop" : "target" });
        closePosition(h.symbol, q.price);
      }
    }
    if (triggered.length > 0) {
      setAutoClosedNotices((prev) => [...prev, ...triggered]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quotes]);

  // Pre-fills the stop-loss/take-profit inputs whenever a holding's detail
  // modal is opened (not on every quote refresh, so in-progress edits stick).
  useEffect(() => {
    if (!selectedHolding) return;
    const h = holdings.find((x) => x.symbol === selectedHolding.symbol) ?? selectedHolding;
    setSlInput(h.stopLoss != null ? String(h.stopLoss) : "");
    setTpInput(h.takeProfit != null ? String(h.takeProfit) : "");
    setSlTpError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedHolding?.symbol]);

  const { totalValue, dayChangePercent, totalPnl, totalCost } = useMemo(() => {
    let holdingsValue = 0;
    let exposure = 0;
    let weightedChange = 0;
    let cost = 0;
    for (const h of holdings) {
      const q = quotes[h.symbol];
      const price = q?.price ?? h.avgPrice;
      const value = price * h.shares;
      holdingsValue += value;
      exposure += Math.abs(value);
      cost += h.avgPrice * h.shares;
      if (q?.change_percent != null) weightedChange += value * q.change_percent;
    }
    const total = cash + holdingsValue;
    return {
      totalValue: total,
      dayChangePercent: exposure > 0 ? weightedChange / exposure : 0,
      totalPnl: holdingsValue - cost,
      totalCost: cost,
    };
  }, [cash, holdings, quotes]);

  const isUp = dayChangePercent >= 0;

  const txPnl = (tx) => {
    if (tx.pnl != null) return tx.pnl;
    const h = holdings.find((x) => x.symbol === tx.symbol);
    const q = quotes[tx.symbol];
    const refPrice = q?.price ?? h?.avgPrice ?? tx.price;
    return tx.side === "buy" ? (refPrice - tx.price) * tx.shares : (tx.price - refPrice) * tx.shares;
  };

  const holdingModal = selectedHolding && (() => {
    const h = holdings.find((x) => x.symbol === selectedHolding.symbol) ?? selectedHolding;
    const q = quotes[h.symbol];
    const price = q?.price ?? h.avgPrice;
    const isShort = h.shares < 0;
    const pnl = (price - h.avgPrice) * h.shares;
    const pnlPercent = ((price - h.avgPrice) / h.avgPrice) * 100 * (isShort ? -1 : 1);
    const marketValue = price * Math.abs(h.shares);

    const handleSaveSlTp = () => {
      const sl = slInput.trim() === "" ? null : parseFloat(slInput);
      const tp = tpInput.trim() === "" ? null : parseFloat(tpInput);
      if ((sl !== null && Number.isNaN(sl)) || (tp !== null && Number.isNaN(tp))) {
        setSlTpError(t("portfolio.slTpInvalidNumber"));
        return;
      }
      if (sl !== null && (isShort ? sl <= price : sl >= price)) {
        setSlTpError(t(isShort ? "portfolio.slInvalidShort" : "portfolio.slInvalidLong"));
        return;
      }
      if (tp !== null && (isShort ? tp >= price : tp <= price)) {
        setSlTpError(t(isShort ? "portfolio.tpInvalidShort" : "portfolio.tpInvalidLong"));
        return;
      }
      setSlTpError(null);
      setStopLossTakeProfit(h.symbol, { stopLoss: sl, takeProfit: tp });
    };

    return (
      <div className="chart-modal-overlay" onClick={() => setSelectedHolding(null)}>
        <div className="chart-modal stock-detail-modal" onClick={(e) => e.stopPropagation()}>
          <div className="chart-modal-head">
            <div className="chart-modal-title">
              <span className="symbol">{h.symbol}</span>
            </div>
            <button className="chart-modal-close" onClick={() => setSelectedHolding(null)} aria-label={t("common.close")}>
              ✕
            </button>
          </div>
          <div className="stock-detail-body">
            <div className="stock-detail-top">
              <span className={`badge-pill ${isShort ? "bearish" : "bullish"}`}>
                {isShort ? t("portfolio.short") : t("portfolio.long")}
              </span>
              <span className={`chg-badge ${pnl >= 0 ? "chg-up" : "chg-down"}`}>
                {pnl >= 0 ? "+" : ""}
                {formatPnl(pnl)}
              </span>
            </div>
            <div className="detail-grid">
              <div className="detail-item">
                <span className="detail-label">{t("portfolio.units")}</span>
                <span>{Math.abs(h.shares)}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">{t("portfolio.avgPrice")}</span>
                <span>{formatPrice(h.avgPrice)}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">{t("common.price")}</span>
                <span>{formatPrice(price)}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">{t("portfolio.changePercent")}</span>
                <span style={{ color: pnlPercent >= 0 ? "var(--up)" : "var(--down)" }}>{formatPercent(pnlPercent)}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">{t("portfolio.marketValue")}</span>
                <span>{formatPrice(marketValue)}</span>
              </div>
            </div>

            <div className="sl-tp-section">
              <span className="detail-label">{t("portfolio.slTpTitle")}</span>
              <div className="sl-tp-row">
                <label className="sl-tp-field">
                  <span>{t("portfolio.stopLoss")}</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    step="0.01"
                    className="qty-picker-custom-input sl-tp-input"
                    placeholder="—"
                    value={slInput}
                    onChange={(e) => setSlInput(e.target.value)}
                  />
                </label>
                <label className="sl-tp-field">
                  <span>{t("portfolio.takeProfit")}</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    step="0.01"
                    className="qty-picker-custom-input sl-tp-input"
                    placeholder="—"
                    value={tpInput}
                    onChange={(e) => setTpInput(e.target.value)}
                  />
                </label>
              </div>
              {slTpError && <p className="sl-tp-error">{slTpError}</p>}
              <button type="button" className="btn btn-primary btn-sm btn-block btn-rect" onClick={handleSaveSlTp}>
                {t("common.save")}
              </button>
            </div>

            <button
              className="btn btn-danger btn-block btn-rect"
              onClick={() => {
                closePosition(h.symbol, price);
                setSelectedHolding(null);
              }}
            >
              {t("portfolio.sellAll")}
            </button>
          </div>
        </div>
      </div>
    );
  })();

  const txModal = selectedTx && (
    <div className="chart-modal-overlay" onClick={() => setSelectedTx(null)}>
      <div className="chart-modal stock-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="symbol">{selectedTx.symbol}</span>
          </div>
          <button className="chart-modal-close" onClick={() => setSelectedTx(null)} aria-label={t("common.close")}>
            ✕
          </button>
        </div>
        <div className="stock-detail-body">
          <div className="stock-detail-top">
            <span className={`badge-pill ${selectedTx.side === "buy" ? "bullish" : "bearish"}`}>
              {selectedTx.side === "buy" ? t("portfolio.buy") : t("portfolio.sell")}
            </span>
            <span className={`chg-badge ${txPnl(selectedTx) >= 0 ? "chg-up" : "chg-down"}`}>
              {txPnl(selectedTx) >= 0 ? "+" : ""}
              {formatPnl(txPnl(selectedTx))}
            </span>
          </div>
          <div className="detail-grid">
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.date")}</span>
              <span>{formatDateTime(selectedTx.timestamp, lang)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">{t("portfolio.units")}</span>
              <span>{Math.abs(selectedTx.shares)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  const autoCloseBanner = autoClosedNotices.length > 0 && (
    <div className="auto-close-banner">
      <div className="auto-close-banner-list">
        {autoClosedNotices.map((n, i) => (
          <span key={i}>
            {t(n.kind === "stop" ? "portfolio.autoClosedStop" : "portfolio.autoClosedTarget")
              .replace("{symbol}", n.symbol)
              .replace("{price}", formatPrice(n.price))}
          </span>
        ))}
      </div>
      <button
        type="button"
        className="auto-close-banner-dismiss"
        onClick={() => setAutoClosedNotices([])}
        aria-label={t("common.close")}
      >
        ✕
      </button>
    </div>
  );

  const portfolioModal = portfolioModalOpen && (
    <PortfolioModal
      cash={cash}
      holdings={holdings}
      quotes={quotes}
      transactions={transactions}
      totalValue={totalValue}
      totalPnl={totalPnl}
      dayChangePercent={dayChangePercent}
      onClose={() => setPortfolioModalOpen(false)}
    />
  );

  if (isMt5) {
    const allTx = [...transactions].reverse();
    const rows =
      mt5Tab === "positions"
        ? holdings.map((h) => {
            const q = quotes[h.symbol];
            const price = q?.price ?? h.avgPrice;
            const isShort = h.shares < 0;
            const vol = Math.abs(h.shares);
            const pnl = (price - h.avgPrice) * h.shares;
            return (
              <div className="mt5-row" key={h.symbol} onClick={() => setSelectedHolding(h)}>
                <div className="mt5-row-line1">
                  <span className="mt5-row-symbol">{h.symbol}</span>
                  <span className={`mt5-row-pnl ${pnl >= 0 ? "is-up" : "is-down"}`}>
                    {pnl >= 0 ? "+" : ""}
                    {formatPnl(pnl)}
                  </span>
                </div>
                <div className="mt5-row-line2">
                  <span className={`mt5-row-side ${isShort ? "is-down" : "is-up"}`}>
                    {vol} {isShort ? t("portfolio.sell") : t("portfolio.buy")}
                  </span>
                  <span className="mt5-row-prices">
                    {formatPrice(h.avgPrice)} → {formatPrice(price)}
                  </span>
                </div>
              </div>
            );
          })
        : allTx.map((tx) => {
            const pnl = txPnl(tx);
            return (
              <div className="mt5-row" key={tx.id} onClick={() => setSelectedTx(tx)}>
                <div className="mt5-row-line1">
                  <span className="mt5-row-symbol">{tx.symbol}</span>
                  <span className={`mt5-row-pnl ${pnl >= 0 ? "is-up" : "is-down"}`}>
                    {pnl >= 0 ? "+" : ""}
                    {formatPnl(pnl)}
                  </span>
                </div>
                <div className="mt5-row-line2">
                  <span className={`mt5-row-side ${tx.side === "sell" ? "is-down" : "is-up"}`}>
                    {Math.abs(tx.shares)} {tx.side === "buy" ? t("portfolio.buy") : t("portfolio.sell")} {formatPrice(tx.price)}
                  </span>
                  <span className="mt5-row-prices">{formatDateTime(tx.timestamp, lang)}</span>
                </div>
              </div>
            );
          });

    return (
      <>
        <div className="screen mt5-trade-screen">
          {autoCloseBanner}
          <div className="mt5-tabs">
            <button
              type="button"
              className={`mt5-tab ${mt5Tab === "positions" ? "active" : ""}`}
              onClick={() => setMt5Tab("positions")}
            >
              {t("portfolio.positionsTab")}
              {holdings.length > 0 ? ` (${holdings.length})` : ""}
            </button>
            <button
              type="button"
              className={`mt5-tab ${mt5Tab === "history" ? "active" : ""}`}
              onClick={() => setMt5Tab("history")}
            >
              {t("portfolio.historyTab")}
            </button>
          </div>

          <button type="button" className="portfolio-link-title" onClick={() => setPortfolioModalOpen(true)}>
            {t("portfolio.myPortfolio")}
          </button>

          <div className="mt5-list">
            {rows.length === 0 ? <div className="mt5-empty">{t("portfolio.empty")}</div> : rows}
          </div>

          <div className="mt5-summary-bar">
            <div className="mt5-summary-item">
              <span className="mt5-summary-label">{t("portfolio.balance")}</span>
              <span className="mt5-summary-value">{formatPrice(cash)}</span>
            </div>
            <div className="mt5-summary-item">
              <span className="mt5-summary-label">{t("portfolio.equity")}</span>
              <span className={`mt5-summary-value ${totalPnl >= 0 ? "is-up" : "is-down"}`}>{formatPrice(totalValue)}</span>
            </div>
          </div>
        </div>
        {holdingModal}
        {txModal}
        {portfolioModal}
      </>
    );
  }

  return (
    <div className="screen">
      {autoCloseBanner}
      <div className="portfolio-hero">
        {isAdvanced && <div className="portfolio-hero-label">{t("portfolio.value")}</div>}
        <div className="portfolio-hero-value">{formatPrice(totalValue)}</div>
        {totalCost > 0 && (
          <div className={`portfolio-hero-pnl ${totalPnl >= 0 ? "is-up" : "is-down"}`}>
            {totalPnl >= 0 ? "+" : ""}
            {formatPnl(totalPnl)}
          </div>
        )}
        {isAdvanced && (
          <div className="portfolio-hero-badges">
            <span className={`badge-pill ${isUp ? "bullish" : "bearish"}`}>{formatPercent(dayChangePercent)}</span>
          </div>
        )}
        {isAdvanced && (
          <div className="portfolio-hero-meta">
            {t("portfolio.cashAvailable")} {formatPrice(cash)}
          </div>
        )}
      </div>

      <button type="button" className="portfolio-link-title" onClick={() => setPortfolioModalOpen(true)}>
        {t("portfolio.myPortfolio")}
      </button>

      {holdings.length > 0 && (
        <div className="holdings-list">
          {holdings.map((h) => {
            const q = quotes[h.symbol];
            const price = q?.price ?? h.avgPrice;
            const isShort = h.shares < 0;
            const pnl = (price - h.avgPrice) * h.shares;
            return (
              <div className="holding-row" key={h.symbol} onClick={() => setSelectedHolding(h)}>
                <div className="holding-main">
                  <div className="holding-symbol">
                    {h.symbol}
                    {isShort && <span className="badge-pill bearish holding-short-badge">{t("portfolio.short")}</span>}
                  </div>
                </div>
                <div className="holding-side">
                  <div className="holding-price">{formatPrice(price)}</div>
                  <div className={`holding-pnl ${pnl >= 0 ? "is-up" : "is-down"}`}>
                    {pnl >= 0 ? "+" : ""}
                    {formatPnl(pnl)}
                  </div>
                </div>
                <button
                  className="holding-remove"
                  title={t("portfolio.sellAll")}
                  onClick={(e) => {
                    e.stopPropagation();
                    closePosition(h.symbol, price);
                  }}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}

      {holdings.length === 0 && isAdvanced && <p className="disclaimer-note">{t("portfolio.empty")}</p>}

      {transactions.length > 0 && (() => {
        const allTx = [...transactions].reverse();
        const HISTORY_PREVIEW_COUNT = 3;
        const hasMore = allTx.length > HISTORY_PREVIEW_COUNT;
        const visibleTx = showAllHistory ? allTx : allTx.slice(0, HISTORY_PREVIEW_COUNT);
        return (
          <div className="trade-history">
            <div className="trade-history-title">{t("portfolio.historyTitle")}</div>
            <div className={`trade-history-list ${showAllHistory ? "trade-history-list-expanded" : ""}`}>
              {visibleTx.map((tx) => {
                const pnl = txPnl(tx);
                return (
                  <button className="stock-row trade-history-row" key={tx.id} onClick={() => setSelectedTx(tx)}>
                    <span className="stock-row-symbol">{tx.symbol}</span>
                    <span className={`chg-badge ${pnl >= 0 ? "chg-up" : "chg-down"}`}>
                      {pnl >= 0 ? "+" : ""}
                      {formatPnl(pnl)}
                    </span>
                  </button>
                );
              })}
            </div>
            {hasMore && (
              <button className="btn btn-ghost btn-sm btn-block btn-rect" onClick={() => setShowAllHistory((v) => !v)}>
                {showAllHistory ? t("portfolio.showLessHistory") : t("portfolio.showAllHistory")}
              </button>
            )}
          </div>
        );
      })()}

      {holdingModal}
      {txModal}
      {portfolioModal}
    </div>
  );
}
