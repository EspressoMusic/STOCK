import { useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { getCandles, getQuote, searchSymbols } from "../api";
import { formatPrice, formatPercent } from "../format";
import { useLang } from "../context/LangContext";
import usePortfolio from "../hooks/usePortfolio";
import { ThickCandlestickSeries } from "../lib/thickCandlestickSeries";

const QTY_PRESETS = [1, 5, 10, 25, 50, 100];
const EMA_PERIOD = 50;
const EMA_COLOR = "#000000";
const CHART_BG = "#f5ecd8";
const VISIBLE_BARS = 40;

// Same formula as the backend's technical-scan EMA (pandas .ewm(span, adjust=False).mean()).
function computeEma(candles, period) {
  if (candles.length === 0) return [];
  const alpha = 2 / (period + 1);
  let prev = candles[0].close;
  return candles.map((c, i) => {
    prev = i === 0 ? c.close : c.close * alpha + prev * (1 - alpha);
    return { time: c.time, value: prev };
  });
}

function readThemeColors() {
  const style = getComputedStyle(document.documentElement);
  const get = (name) => style.getPropertyValue(name).trim();
  return {
    bg: get("--surface"),
    text: get("--text"),
    border: get("--border"),
    up: get("--up"),
    down: get("--down"),
  };
}

export default function ChartScanScreen() {
  const { t } = useLang();
  const { cash, holdings, buy, sell } = usePortfolio();

  const [symbol, setSymbol] = useState(() => holdings[0]?.symbol || "AAPL");
  const [query, setQuery] = useState("");
  const [quote, setQuote] = useState(null);
  const [quoteError, setQuoteError] = useState(null);
  const [shares, setShares] = useState(1);

  const [timeframe, setTimeframe] = useState("1h");
  const [tfOpen, setTfOpen] = useState(false);
  const tfRef = useRef(null);
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const emaSeriesRef = useRef(null);
  const [chartLoading, setChartLoading] = useState(true);
  const [chartError, setChartError] = useState(null);

  const [trading, setTrading] = useState(false);
  const [tradeMessage, setTradeMessage] = useState(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [qtyPickerOpen, setQtyPickerOpen] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [searching, setSearching] = useState(false);

  const TIMEFRAMES = [
    { value: "5m", label: t("chartScan.tf5m") },
    { value: "15m", label: t("chartScan.tf15m") },
    { value: "30m", label: t("chartScan.tf30m") },
    { value: "1h", label: t("chartScan.tf1h") },
    { value: "4h", label: t("chartScan.tf4h") },
    { value: "1d", label: t("chartScan.tf1d") },
    { value: "1w", label: t("chartScan.tf1w") },
  ];
  const currentTf = TIMEFRAMES.find((tf) => tf.value === timeframe);

  const holding = holdings.find((h) => h.symbol === symbol);
  const ownedShares = holding?.shares || 0;
  const positionProfit = holding && quote?.price != null ? (quote.price - holding.avgPrice) * holding.shares : 0;

  useEffect(() => {
    if (!tfOpen) return;
    const onClickOutside = (e) => {
      if (tfRef.current && !tfRef.current.contains(e.target)) setTfOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [tfOpen]);

  useEffect(() => {
    setQuote(null);
    setQuoteError(null);
    getQuote(symbol)
      .then(setQuote)
      .catch((err) => setQuoteError(err.message));
  }, [symbol]);

  useEffect(() => {
    const colors = readThemeColors();
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: CHART_BG }, textColor: colors.text, attributionLogo: false },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      timeScale: { visible: false },
      rightPriceScale: { visible: false },
    });
    const series = chart.addCustomSeries(new ThickCandlestickSeries(), {
      upColor: colors.up,
      downColor: colors.down,
      borderColor: "#000000",
      wickColor: "#000000",
      borderWidth: 3,
      wickWidth: 3,
    });
    const emaSeries = chart.addSeries(LineSeries, {
      color: EMA_COLOR,
      lineWidth: 4,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    emaSeriesRef.current = emaSeries;

    const observer = new MutationObserver(() => {
      const c = readThemeColors();
      chart.applyOptions({
        layout: { background: { color: CHART_BG }, textColor: c.text },
        timeScale: { borderColor: c.border },
        rightPriceScale: { borderColor: c.border },
      });
      series.applyOptions({
        upColor: c.up,
        downColor: c.down,
        borderColor: "#000000",
        wickColor: "#000000",
      });
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, []);

  useEffect(() => {
    setChartLoading(true);
    setChartError(null);
    getCandles(symbol, timeframe)
      .then((data) => {
        seriesRef.current?.setData(
          data.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }))
        );
        emaSeriesRef.current?.setData(computeEma(data.candles, EMA_PERIOD));
        const total = data.candles.length;
        if (total > VISIBLE_BARS) {
          chartRef.current?.timeScale().setVisibleLogicalRange({ from: total - VISIBLE_BARS, to: total - 1 });
        } else {
          chartRef.current?.timeScale().fitContent();
        }
      })
      .catch((err) => setChartError(err.message))
      .finally(() => setChartLoading(false));
  }, [symbol, timeframe]);

  const closePicker = () => {
    setPickerOpen(false);
    setQuery("");
    setSuggestions([]);
  };

  const selectSymbol = (sym) => {
    setSymbol(sym.trim().toUpperCase());
    setQuery("");
    setSuggestions([]);
    setTradeMessage(null);
    setPickerOpen(false);
  };

  const handleSearch = (e) => {
    e.preventDefault();
    const next = suggestions[0]?.symbol || query.trim();
    if (!next) return;
    selectSymbol(next);
  };

  useEffect(() => {
    if (!pickerOpen) return;
    const trimmed = query.trim();
    if (!trimmed) {
      setSuggestions([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const id = setTimeout(() => {
      searchSymbols(trimmed)
        .then((data) => setSuggestions(data.results || []))
        .catch(() => setSuggestions([]))
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(id);
  }, [query, pickerOpen]);

  const handleBuy = () => {
    const qty = shares;
    const cost = qty * (quote?.price || 0);
    if (cost > cash) {
      setTradeMessage({ type: "error", text: t("stockDetail.insufficientCash") });
      return;
    }
    setTrading(true);
    buy(symbol, quote?.name, qty, quote.price);
    setTrading(false);
    setTradeMessage({ type: "success", text: t("stockDetail.saved") });
  };

  const handleSell = () => {
    const qty = shares;
    if (qty > ownedShares) {
      setTradeMessage({ type: "error", text: t("chartScan.notEnoughShares") });
      return;
    }
    setTrading(true);
    sell(symbol, qty, quote?.price || 0);
    setTrading(false);
    setTradeMessage({ type: "success", text: t("chartScan.sold") });
  };

  const handleClosePosition = () => {
    if (!quote?.price || ownedShares <= 0) return;
    setTrading(true);
    sell(symbol, ownedShares, quote.price);
    setTrading(false);
    setTradeMessage({ type: "success", text: t("chartScan.sold") });
  };

  return (
    <div className="chart-scan-screen">
      <div className="chart-full-container">
        {chartError && <p className="state-msg chart-state-msg">{chartError}</p>}
        {chartLoading && !chartError && <p className="chart-loading">{t("common.loading")}</p>}
        <div ref={containerRef} className="chart-container" />
      </div>

      <div className="chart-scan-topbar">
        <div className="chart-scan-topbar-row">
          <div className="tf-dropdown" ref={tfRef}>
            <button
              type="button"
              className={`tf-dropdown-trigger${tfOpen ? " open" : ""}`}
              onClick={() => setTfOpen((open) => !open)}
            >
              {currentTf?.label}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            {tfOpen && (
              <div className="tf-dropdown-menu">
                {TIMEFRAMES.map((tf) => (
                  <button
                    key={tf.value}
                    type="button"
                    className={timeframe === tf.value ? "active" : ""}
                    onClick={() => {
                      setTimeframe(tf.value);
                      setTfOpen(false);
                    }}
                  >
                    {tf.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button type="button" className="chart-scan-symbol-btn" onClick={() => setPickerOpen(true)}>
            <span className="symbol">{symbol}</span>
          </button>

          <div className="chart-scan-topbar-spacer" />
        </div>

        {quote && (
          <div className="chart-scan-price-block">
            <span className="stock-detail-price">{formatPrice(quote.price)}</span>
            <span className={`chg-badge ${quote.change_percent >= 0 ? "chg-up" : "chg-down"}`}>
              {formatPercent(quote.change_percent)}
            </span>
          </div>
        )}
        {quoteError && (
          <p className="disclaimer-note" style={{ color: "var(--down)" }}>
            {t("chartScan.notFound")}
          </p>
        )}
      </div>

      {(ownedShares > 0 || tradeMessage) && (
        <div className="chart-scan-status">
          {ownedShares > 0 && (
            <div className="chart-scan-position">
              <span className="settings-row-sub">
                {t("chartScan.owned")}: {ownedShares}
              </span>
              <span className={`position-pnl ${positionProfit >= 0 ? "is-up" : "is-down"}`}>
                {positionProfit >= 0 ? "+" : ""}
                {formatPrice(positionProfit)}
              </span>
              <button
                type="button"
                className="position-close-btn"
                onClick={handleClosePosition}
                disabled={trading}
                aria-label={t("chartScan.closePosition")}
              >
                ✕
              </button>
            </div>
          )}
          {tradeMessage && (
            <p
              className={tradeMessage.type === "success" ? "stock-buy-saved" : "disclaimer-note"}
              style={tradeMessage.type === "error" ? { color: "var(--down)" } : undefined}
            >
              {tradeMessage.text}
            </p>
          )}
        </div>
      )}

      <div className="chart-scan-floating-bar">
        <button className="btn btn-primary btn-xl btn-rect" onClick={handleBuy} disabled={trading || !quote?.price}>
          {t("chartScan.buy")}
        </button>
        <button
          type="button"
          className="qty-box"
          onClick={() => setQtyPickerOpen(true)}
          aria-label={t("chartScan.quantity")}
        >
          {shares}
        </button>
        <button
          className="btn btn-ghost btn-xl btn-rect"
          onClick={handleSell}
          disabled={trading || !quote?.price || ownedShares <= 0}
        >
          {t("chartScan.sell")}
        </button>
      </div>

      {pickerOpen && (
        <div className="chart-modal-overlay" onClick={closePicker}>
          <div className="chart-modal symbol-picker-modal" onClick={(e) => e.stopPropagation()}>
            <div className="chart-modal-head">
              <div className="chart-modal-title">
                <span className="symbol">{t("chartScan.chooseSymbol")}</span>
              </div>
              <button className="chart-modal-close" onClick={closePicker} aria-label={t("common.close")}>
                ✕
              </button>
            </div>
            <form className="add-holding-row symbol-picker-form" onSubmit={handleSearch}>
              <input
                autoFocus
                type="text"
                placeholder={t("chartScan.searchPlaceholder")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </form>
            {query.trim() && (
              <div className="symbol-picker-results">
                {searching && <p className="state-msg">{t("common.loading")}</p>}
                {!searching && suggestions.length === 0 && (
                  <p className="disclaimer-note">{t("common.noResults")}</p>
                )}
                {!searching &&
                  suggestions.map((m) => (
                    <button
                      key={m.symbol}
                      type="button"
                      className="symbol-picker-result"
                      onClick={() => selectSymbol(m.symbol)}
                    >
                      <span className="symbol-picker-result-symbol">{m.symbol}</span>
                      {m.name && <span className="symbol-picker-result-name">{m.name}</span>}
                    </button>
                  ))}
              </div>
            )}
          </div>
        </div>
      )}

      {qtyPickerOpen && (
        <div className="chart-modal-overlay" onClick={() => setQtyPickerOpen(false)}>
          <div className="chart-modal qty-picker-modal" onClick={(e) => e.stopPropagation()}>
            <div className="chart-modal-head">
              <div className="chart-modal-title">
                <span className="symbol">{t("chartScan.quantity")}</span>
              </div>
              <button className="chart-modal-close" onClick={() => setQtyPickerOpen(false)} aria-label={t("common.close")}>
                ✕
              </button>
            </div>

            <div className="qty-picker-presets">
              {QTY_PRESETS.map((n) => (
                <button
                  key={n}
                  type="button"
                  className={`qty-preset-btn ${shares === n ? "active" : ""}`}
                  onClick={() => {
                    setShares(n);
                    setQtyPickerOpen(false);
                  }}
                >
                  {n}
                </button>
              ))}
            </div>

            <div className="qty-picker-custom">
              <span className="detail-label">{t("chartScan.customQuantity")}</span>
              <input
                autoFocus
                type="number"
                min="1"
                step="1"
                inputMode="numeric"
                className="qty-picker-custom-input"
                value={shares}
                onChange={(e) => setShares(Math.max(1, parseInt(e.target.value, 10) || 1))}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
