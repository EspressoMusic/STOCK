import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createChart, createSeriesMarkers, LineSeries, LineStyle } from "lightweight-charts";
import { getCandles, getQuote, searchSymbols } from "../api";
import { formatPrice, formatPercent } from "../format";
import { useLang } from "../context/LangContext";
import { useNav } from "../context/NavContext";
import usePortfolio from "../hooks/usePortfolio";
import useWatchlist from "../hooks/useWatchlist";
import { TRADING_ENABLED } from "../features";
import { ThickCandlestickSeries } from "../lib/thickCandlestickSeries";
import { detectZonesFrom } from "../lib/zones";
import { computeEma } from "../lib/ema";
import { ZonesPrimitive } from "../lib/zonesPrimitive";

const QTY_PRESETS = [1, 5, 10, 25, 50, 100];
const EMA_PERIOD = 50;
const EMA_COLOR = "#000000";
const CHART_BG = "#f5ecd8";
const VISIBLE_BARS = 40;
const LAST_SYMBOL_KEY = "chart-scan-last-symbol";
const QUOTE_REFRESH_INTERVAL_MS = 15000;
const CANDLES_RETRY_MS = 5000;
// chartError value for "the server couldn't be reached" (fetch rejects with a
// TypeError), which is retried automatically instead of shown as a raw message.
const NETWORK_ERROR = "network";
const DEFAULT_PRICE_MARGINS = { top: 0.1, bottom: 0.1 };
const VERTICAL_ZOOM_SENSITIVITY = 0.8;
const ZONES_ON_KEY = "chart-zones-on";
const ZONES_MIN_TOUCHES_KEY = "chart-zones-min-touches";
const ZONE_TOUCH_OPTIONS = [2, 3, 4];
const DEFAULT_ZONE_TOUCHES = 3;
const ZONES_ANCHOR_KEY = "chart-zones-anchor";

// Start-line candle times per symbol ({ AAPL: unixSeconds }), so the line is still
// there the next time the symbol is opened.
function readZoneAnchors() {
  try {
    const parsed = JSON.parse(localStorage.getItem(ZONES_ANCHOR_KEY));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

// The last-price badge drops the "$" so it stays narrow.
function formatAxisPrice(value) {
  return formatPrice(value).replace("$", "");
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function roundPrice(value) {
  if (value < 1) return Math.round(value * 10000) / 10000;
  return Math.round(value * 100) / 100;
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
  const { params } = useNav();
  const { cash, holdings, buy, sell, closePosition, setStopLossTakeProfit } = usePortfolio();
  const watchlist = useWatchlist();

  const [symbol, setSymbol] = useState(
    () => params?.symbol || localStorage.getItem(LAST_SYMBOL_KEY) || watchlist.items[0]?.symbol || "AAPL"
  );
  const saved = watchlist.isSaved(symbol);
  const [query, setQuery] = useState("");
  const [quote, setQuote] = useState(null);
  const [quoteError, setQuoteError] = useState(null);
  const [shares, setShares] = useState(1);

  const [timeframe, setTimeframe] = useState(() => params?.timeframe || "1h");
  // A scan result (e.g. Shilo) can open the chart with the level it found and the candles
  // that touched it: { level, kind: "support" | "resistance", touchTimes }. Only drawn
  // while that result's symbol is the one on screen.
  const pattern = params?.pattern && params.symbol === symbol ? params.pattern : null;
  const [tfOpen, setTfOpen] = useState(false);
  const tfRef = useRef(null);
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const emaSeriesRef = useRef(null);
  const markersRef = useRef(null);
  const candlesRef = useRef([]);
  const updatePriceLineYRef = useRef(() => {});
  const lastCloseRef = useRef(null);
  const [chartLoading, setChartLoading] = useState(true);
  const [chartError, setChartError] = useState(null);
  const [candlesRetry, setCandlesRetry] = useState(0);
  const [priceLineY, setPriceLineY] = useState(null);
  const [lastPriceY, setLastPriceY] = useState(null);
  const [lastPriceX, setLastPriceX] = useState(null);
  const [stopLossY, setStopLossY] = useState(null);
  const [takeProfitY, setTakeProfitY] = useState(null);
  const [dragLine, setDragLine] = useState(null);
  const [dragY, setDragY] = useState(null);
  const [dragPrice, setDragPrice] = useState(null);
  const [slInput, setSlInput] = useState("");
  const [tpInput, setTpInput] = useState("");
  const [slTpError, setSlTpError] = useState(null);

  const [trading, setTrading] = useState(false);
  const [tradeMessage, setTradeMessage] = useState(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [qtyPickerOpen, setQtyPickerOpen] = useState(false);
  const [positionMenuOpen, setPositionMenuOpen] = useState(false);
  const [closeQty, setCloseQty] = useState(1);
  const [suggestions, setSuggestions] = useState([]);
  const [searching, setSearching] = useState(false);

  const [zonesOn, setZonesOn] = useState(() => localStorage.getItem(ZONES_ON_KEY) === "1");
  const [zonesMinTouches, setZonesMinTouches] = useState(() => {
    const stored = Number(localStorage.getItem(ZONES_MIN_TOUCHES_KEY));
    return ZONE_TOUCH_OPTIONS.includes(stored) ? stored : DEFAULT_ZONE_TOUCHES;
  });
  const [zonesMenuOpen, setZonesMenuOpen] = useState(false);
  const zonesMenuRef = useRef(null);
  const zonesRef = useRef(null);
  // Bumped whenever candlesRef gets new data, so the zones are recomputed for it.
  const [candlesVersion, setCandlesVersion] = useState(0);

  // The start line: zones and breakout+retest are only looked for from its candle on.
  // Kept per symbol as a candle time, so it lands on the same moment on every timeframe.
  const [zoneAnchors, setZoneAnchors] = useState(readZoneAnchors);
  const anchorTime = zoneAnchors[symbol] ?? null;
  // true while waiting for the tap on the chart that places the line.
  const [placingAnchor, setPlacingAnchor] = useState(false);
  const [draggingAnchor, setDraggingAnchor] = useState(false);
  // The line's x in px and the chart's width, or null while it isn't drawn.
  const [anchorPos, setAnchorPos] = useState(null);

  const setAnchorTime = useCallback(
    (time) =>
      setZoneAnchors((all) => {
        const next = { ...all };
        if (time == null) delete next[symbol];
        else next[symbol] = time;
        return next;
      }),
    [symbol]
  );

  // The first candle at or after the line; a line past the last candle stays on it.
  const anchorIdx = useMemo(() => {
    const candles = candlesRef.current;
    if (anchorTime == null || !candles.length) return null;
    const idx = candles.findIndex((c) => c.time >= anchorTime);
    return idx === -1 ? candles.length - 1 : idx;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorTime, candlesVersion]);

  const autoZones = useMemo(
    () =>
      zonesOn
        ? detectZonesFrom(candlesRef.current, anchorIdx ?? 0, { minTouches: zonesMinTouches })
        : { zones: [], events: [] },
    // candlesVersion stands in for candlesRef.current, which a ref can't signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [zonesOn, zonesMinTouches, anchorIdx, candlesVersion]
  );

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

  // With trading off there is never a position, so the entry/SL/TP lines and
  // the position menu below all stay hidden.
  const holding = TRADING_ENABLED ? holdings.find((h) => h.symbol === symbol) : null;
  const ownedShares = holding?.shares || 0;
  const isShort = ownedShares < 0;
  const positionQty = Math.abs(ownedShares);
  const positionProfit = holding && quote?.price != null ? (quote.price - holding.avgPrice) * holding.shares : 0;

  useEffect(() => {
    if (params?.symbol) setSymbol(params.symbol);
    if (params?.timeframe) setTimeframe(params.timeframe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  useEffect(() => {
    localStorage.setItem(LAST_SYMBOL_KEY, symbol);
  }, [symbol]);

  useEffect(() => {
    if (!tfOpen) return;
    const onClickOutside = (e) => {
      if (tfRef.current && !tfRef.current.contains(e.target)) setTfOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [tfOpen]);

  useEffect(() => {
    if (!zonesMenuOpen) return;
    const onClickOutside = (e) => {
      if (zonesMenuRef.current && !zonesMenuRef.current.contains(e.target)) setZonesMenuOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [zonesMenuOpen]);

  useEffect(() => {
    localStorage.setItem(ZONES_ON_KEY, zonesOn ? "1" : "0");
  }, [zonesOn]);

  useEffect(() => {
    localStorage.setItem(ZONES_MIN_TOUCHES_KEY, String(zonesMinTouches));
  }, [zonesMinTouches]);

  useEffect(() => {
    localStorage.setItem(ZONES_ANCHOR_KEY, JSON.stringify(zoneAnchors));
  }, [zoneAnchors]);

  // While placing, a tap on the chart (panning still works) puts the line on the left
  // edge of the tapped candle.
  useEffect(() => {
    const chart = chartRef.current;
    if (!placingAnchor || !chart) return;
    const onClick = (param) => {
      const candles = candlesRef.current;
      if (param.logical == null || !candles.length) return;
      setAnchorTime(candles[clamp(param.logical, 0, candles.length - 1)].time);
      setPlacingAnchor(false);
    };
    chart.subscribeClick(onClick);
    return () => chart.unsubscribeClick(onClick);
  }, [placingAnchor, setAnchorTime]);

  // Keeps the line on its candle's left edge as the chart pans and zooms.
  useEffect(() => {
    const timeScale = chartRef.current?.timeScale();
    if (!timeScale || !zonesOn || anchorIdx == null) {
      setAnchorPos(null);
      return;
    }
    const update = () => {
      const x = timeScale.logicalToCoordinate(anchorIdx);
      const width = containerRef.current?.clientWidth;
      setAnchorPos(x == null || !width ? null : { x: x - timeScale.options().barSpacing / 2, width });
    };
    update();
    timeScale.subscribeVisibleLogicalRangeChange(update);
    window.addEventListener("resize", update);
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(update);
      window.removeEventListener("resize", update);
    };
  }, [zonesOn, anchorIdx, candlesVersion]);

  useEffect(() => {
    let cancelled = false;
    setQuote(null);
    setQuoteError(null);
    const fetchQuote = () => {
      getQuote(symbol)
        .then((q) => {
          if (cancelled) return;
          setQuote(q);
          setQuoteError(null);
        })
        .catch((err) => {
          // A network failure (server down/restarting) isn't "symbol not found" —
          // keep the last quote and let the next refresh try again.
          if (!cancelled && !(err instanceof TypeError)) setQuoteError(err.message);
        });
    };
    fetchQuote();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") fetchQuote();
    }, QUOTE_REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
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
      // The last-price line is drawn as an overlay starting at the last candle,
      // instead of the library's line that spans the whole pane.
      priceLineVisible: false,
      lastValueVisible: false,
    });
    series.priceScale().applyOptions({ scaleMargins: DEFAULT_PRICE_MARGINS });
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
    markersRef.current = createSeriesMarkers(series, []);
    const zones = new ZonesPrimitive();
    series.attachPrimitive(zones);
    zonesRef.current = zones;

    const onRangeChange = () => updatePriceLineYRef.current();
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange);
    window.addEventListener("resize", onRangeChange);

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

    // Pressing near the left/right edge of the chart and dragging vertically zooms the
    // price scale (drag down compresses it, drag up expands it) instead of panning, which
    // lightweight-charts would otherwise do for any single-pointer drag. We intercept in
    // the capture phase so the library's own pan listener (bound on its internal canvas)
    // never sees the move events once we've locked the gesture to the vertical axis.
    // Restricted to the edge zones so dragging in the middle of the chart still pans normally.
    const el = containerRef.current;
    const EDGE_ZONE_PX = 32;
    const gesture = { active: false, axis: null, startX: 0, startY: 0, baseTop: 0.1, baseBottom: 0.1 };

    const onPointerDown = (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const rect = el.getBoundingClientRect();
      const distFromEdge = Math.min(e.clientX - rect.left, rect.right - e.clientX);
      if (distFromEdge > EDGE_ZONE_PX) return;
      const opts = series.priceScale().options();
      gesture.active = true;
      gesture.axis = null;
      gesture.startX = e.clientX;
      gesture.startY = e.clientY;
      gesture.baseTop = opts.scaleMargins?.top ?? DEFAULT_PRICE_MARGINS.top;
      gesture.baseBottom = opts.scaleMargins?.bottom ?? DEFAULT_PRICE_MARGINS.bottom;
    };

    const onPointerMove = (e) => {
      if (!gesture.active) return;
      const dx = e.clientX - gesture.startX;
      const dy = e.clientY - gesture.startY;
      if (gesture.axis === null) {
        if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
        gesture.axis = Math.abs(dy) > Math.abs(dx) ? "vertical" : "horizontal";
      }
      if (gesture.axis !== "vertical") return;
      e.preventDefault();
      e.stopPropagation();
      const height = el.clientHeight || 300;
      const factor = clamp(dy / height, -0.9, 0.9) * VERTICAL_ZOOM_SENSITIVITY;
      series.priceScale().applyOptions({
        autoScale: false,
        scaleMargins: {
          top: clamp(gesture.baseTop + factor, 0.02, 0.45),
          bottom: clamp(gesture.baseBottom + factor, 0.02, 0.45),
        },
      });
    };

    const onPointerEnd = () => {
      gesture.active = false;
      gesture.axis = null;
    };

    el.addEventListener("pointerdown", onPointerDown, { capture: true });
    el.addEventListener("pointermove", onPointerMove, { capture: true, passive: false });
    el.addEventListener("pointerup", onPointerEnd, { capture: true });
    el.addEventListener("pointercancel", onPointerEnd, { capture: true });
    el.addEventListener("pointerleave", onPointerEnd, { capture: true });

    return () => {
      observer.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRangeChange);
      window.removeEventListener("resize", onRangeChange);
      el.removeEventListener("pointerdown", onPointerDown, { capture: true });
      el.removeEventListener("pointermove", onPointerMove, { capture: true });
      el.removeEventListener("pointerup", onPointerEnd, { capture: true });
      el.removeEventListener("pointercancel", onPointerEnd, { capture: true });
      el.removeEventListener("pointerleave", onPointerEnd, { capture: true });
      // Lets effects that drew on the series see it's gone (see the pattern overlay).
      seriesRef.current = null;
      zonesRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer = null;
    setChartLoading(true);
    // keep the "retrying" message up between retries so it doesn't flicker
    setChartError((prev) => (prev === NETWORK_ERROR ? prev : null));
    getCandles(symbol, timeframe)
      .then((data) => {
        if (cancelled) return;
        setChartError(null);
        seriesRef.current?.setData(
          data.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }))
        );
        candlesRef.current = data.candles;
        setCandlesVersion((v) => v + 1);
        emaSeriesRef.current?.setData(computeEma(data.candles, EMA_PERIOD));
        lastCloseRef.current = data.candles.length ? data.candles[data.candles.length - 1].close : null;
        seriesRef.current?.priceScale().applyOptions({ autoScale: true, scaleMargins: DEFAULT_PRICE_MARGINS });
        const total = data.candles.length;
        if (total > VISIBLE_BARS) {
          chartRef.current?.timeScale().setVisibleLogicalRange({ from: total - VISIBLE_BARS, to: total - 1 });
        } else {
          chartRef.current?.timeScale().fitContent();
        }
        updatePriceLineYRef.current();
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof TypeError) {
          setChartError(NETWORK_ERROR);
          retryTimer = setTimeout(() => setCandlesRetry((n) => n + 1), CANDLES_RETRY_MS);
        } else {
          setChartError(err.message);
        }
      })
      .finally(() => {
        if (!cancelled) setChartLoading(false);
      });
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
    };
  }, [symbol, timeframe, candlesRetry]);

  // The scan's level is a price, so it's drawn on every timeframe; the touch markers are
  // 4h candle times, so they only line up with bars on the 4h view.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !pattern || chartLoading) return;
    const colors = readThemeColors();
    const isSupport = pattern.kind === "support";
    const color = isSupport ? colors.up : colors.down;
    const line = series.createPriceLine({
      price: pattern.level,
      color,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
    });
    const touchSecs = timeframe === "4h" ? pattern.touchTimes.map((iso) => Date.parse(iso) / 1000) : [];
    markersRef.current?.setMarkers(
      touchSecs.map((time, i) => ({
        time,
        position: isSupport ? "belowBar" : "aboveBar",
        shape: isSupport ? "arrowUp" : "arrowDown",
        color,
        text: String(i + 1),
      }))
    );
    return () => {
      if (seriesRef.current !== series) return; // chart already torn down (unmount)
      markersRef.current?.setMarkers([]);
      series.removePriceLine(line);
    };
  }, [pattern, timeframe, chartLoading]);

  useEffect(() => {
    zonesRef.current?.setState({
      candles: candlesRef.current,
      zones: autoZones.zones,
      events: autoZones.events,
      texts: { breakout: t("zones.breakout"), retest: t("zones.retest") },
    });
  }, [autoZones, t]);

  useEffect(() => {
    const series = seriesRef.current;

    updatePriceLineYRef.current = () => {
      const height = containerRef.current?.clientHeight;

      if (!series || !holding) {
        setPriceLineY(null);
      } else {
        const y = series.priceToCoordinate(holding.avgPrice);
        if (y == null || !Number.isFinite(y)) {
          setPriceLineY(null);
        } else {
          setPriceLineY(height ? Math.min(Math.max(y, 8), height - 8) : y);
        }
      }

      if (!series || lastCloseRef.current == null) {
        setLastPriceY(null);
      } else {
        const y = series.priceToCoordinate(lastCloseRef.current);
        if (y == null || !Number.isFinite(y)) {
          setLastPriceY(null);
        } else {
          setLastPriceY(height ? Math.min(Math.max(y, 8), height - 8) : y);
        }
      }

      const timeScale = chartRef.current?.timeScale();
      const lastIndex = candlesRef.current?.length ? candlesRef.current.length - 1 : null;
      const x = timeScale && lastIndex != null ? timeScale.logicalToCoordinate(lastIndex) : null;
      if (x == null || !Number.isFinite(x)) {
        setLastPriceX(null);
      } else {
        // Start just past the right edge of the last candle's body.
        setLastPriceX(Math.max(x + timeScale.options().barSpacing / 2, 0));
      }

      if (!series || holding?.stopLoss == null) {
        setStopLossY(null);
      } else {
        const y = series.priceToCoordinate(holding.stopLoss);
        setStopLossY(y == null || !Number.isFinite(y) ? null : height ? Math.min(Math.max(y, 8), height - 8) : y);
      }

      if (!series || holding?.takeProfit == null) {
        setTakeProfitY(null);
      } else {
        const y = series.priceToCoordinate(holding.takeProfit);
        setTakeProfitY(y == null || !Number.isFinite(y) ? null : height ? Math.min(Math.max(y, 8), height - 8) : y);
      }
    };
    updatePriceLineYRef.current();
  }, [holding, ownedShares, isShort, positionQty, positionProfit, t]);

  // One tap draws the zones and their breakout+retest marks; another hides them.
  const toggleZones = () => {
    if (zonesOn) {
      setZonesMenuOpen(false);
      setPlacingAnchor(false);
    }
    setZonesOn((on) => !on);
  };

  // The start-line button: with no line, the next tap on the chart places one; with a
  // line, it removes it (back to analysing every candle). Tapping while placing cancels.
  const toggleAnchor = () => {
    if (placingAnchor) setPlacingAnchor(false);
    else if (anchorTime != null) setAnchorTime(null);
    else setPlacingAnchor(true);
  };

  // Dragging the line sideways moves it to the candle edge nearest the pointer.
  const moveAnchorTo = (clientX) => {
    const timeScale = chartRef.current?.timeScale();
    const rect = containerRef.current?.getBoundingClientRect();
    const candles = candlesRef.current;
    if (!timeScale || !rect || !candles.length) return;
    // The line sits on a candle's left edge; half a bar to the right is inside that candle.
    const x = clamp(clientX - rect.left, 0, rect.width);
    const logical = timeScale.coordinateToLogical(x + timeScale.options().barSpacing / 2);
    if (logical == null) return;
    const idx = clamp(logical, 0, candles.length - 1);
    if (idx !== anchorIdx) setAnchorTime(candles[idx].time);
  };

  const startAnchorDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDraggingAnchor(true);
  };

  const onAnchorDragMove = (e) => {
    if (draggingAnchor) moveAnchorTo(e.clientX);
  };

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
    setTrading(true);
    sell(symbol, quote?.name, qty, quote?.price || 0);
    setTrading(false);
    setTradeMessage({ type: "success", text: t("chartScan.sold") });
  };

  const handleClosePosition = () => {
    if (!quote?.price || ownedShares === 0) return;
    setTrading(true);
    closePosition(symbol, quote.price);
    setTrading(false);
    setTradeMessage({ type: "success", text: t("chartScan.sold") });
  };

  const openPositionMenu = () => {
    if (!holding) return;
    setCloseQty(positionQty || 1);
    if (holding.stopLoss != null) {
      setSlInput(String(holding.stopLoss));
    } else {
      const suggestedStop = roundPrice(isShort ? holding.avgPrice * 1.02 : holding.avgPrice * 0.98);
      setSlInput(String(suggestedStop));
    }
    if (holding.takeProfit != null) {
      setTpInput(String(holding.takeProfit));
    } else {
      const suggestedTarget = roundPrice(isShort ? holding.avgPrice * 0.96 : holding.avgPrice * 1.04);
      setTpInput(String(suggestedTarget));
    }
    setSlTpError(null);
    setPositionMenuOpen(true);
  };

  const handleSaveSlTp = () => {
    if (!holding) return;
    const sl = slInput.trim() === "" ? null : parseFloat(slInput);
    const tp = tpInput.trim() === "" ? null : parseFloat(tpInput);
    if ((sl !== null && Number.isNaN(sl)) || (tp !== null && Number.isNaN(tp))) {
      setSlTpError(t("portfolio.slTpInvalidNumber"));
      return;
    }
    const refPrice = quote?.price ?? holding.avgPrice;
    if (sl !== null && (isShort ? sl <= refPrice : sl >= refPrice)) {
      setSlTpError(t(isShort ? "portfolio.slInvalidShort" : "portfolio.slInvalidLong"));
      return;
    }
    if (tp !== null && (isShort ? tp >= refPrice : tp <= refPrice)) {
      setSlTpError(t(isShort ? "portfolio.tpInvalidShort" : "portfolio.tpInvalidLong"));
      return;
    }
    setSlTpError(null);
    setStopLossTakeProfit(symbol, { stopLoss: sl, takeProfit: tp });
  };

  // Keeps the SL/TP line pinned to the correct side of the current price while
  // dragging (stop below / target above for a long, mirrored for a short), so a
  // drag can never produce a level that would trigger the auto-close immediately.
  const clampLineY = (type, y) => {
    const height = containerRef.current?.clientHeight || 300;
    let minY = 8;
    let maxY = height - 8;
    const pivotPrice = quote?.price ?? holding?.avgPrice;
    const pivotY = pivotPrice != null ? seriesRef.current?.priceToCoordinate(pivotPrice) : null;
    if (pivotY != null && Number.isFinite(pivotY)) {
      const MIN_GAP = 14;
      const wantsBelow = (type === "sl") !== isShort;
      if (wantsBelow) {
        minY = Math.max(minY, pivotY + MIN_GAP);
      } else {
        maxY = Math.min(maxY, pivotY - MIN_GAP);
      }
    }
    return clamp(y, minY, maxY);
  };

  const updateDragFromEvent = (e, type) => {
    const series = seriesRef.current;
    const rect = containerRef.current?.getBoundingClientRect();
    if (!series || !rect) return;
    const y = clampLineY(type, e.clientY - rect.top);
    const price = series.coordinateToPrice(y);
    if (price == null || !Number.isFinite(price)) return;
    setDragY(y);
    setDragPrice(roundPrice(price));
  };

  const startDragLine = (e, type) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragLine(type);
    updateDragFromEvent(e, type);
  };

  const onDragLineMove = (e) => {
    if (!dragLine) return;
    updateDragFromEvent(e, dragLine);
  };

  const endDragLine = () => {
    if (dragLine && dragPrice != null && holding) {
      const next = {
        stopLoss: dragLine === "sl" ? dragPrice : holding.stopLoss ?? null,
        takeProfit: dragLine === "tp" ? dragPrice : holding.takeProfit ?? null,
      };
      setStopLossTakeProfit(symbol, next);
      if (dragLine === "sl") setSlInput(String(dragPrice));
      else setTpInput(String(dragPrice));
    }
    setDragLine(null);
    setDragY(null);
    setDragPrice(null);
  };

  const handleReducePosition = () => {
    if (!quote?.price || !holding) return;
    const qty = Math.min(closeQty, positionQty);
    if (qty <= 0) return;
    setTrading(true);
    if (isShort) {
      buy(symbol, quote?.name, qty, quote.price);
    } else {
      sell(symbol, quote?.name, qty, quote.price);
    }
    setTrading(false);
    setTradeMessage({ type: "success", text: t("chartScan.sold") });
    setPositionMenuOpen(false);
  };

  const handleFullCloseFromMenu = () => {
    handleClosePosition();
    setPositionMenuOpen(false);
  };

  return (
    <div className="chart-scan-screen">
      <div className="chart-full-container">
        {chartError && (
          <p className="state-msg chart-state-msg">
            {chartError === NETWORK_ERROR ? t("common.connectionRetry") : chartError}
          </p>
        )}
        {chartLoading && !chartError && <p className="chart-loading">{t("common.loading")}</p>}
        <div className="chart-container-wrap">
          <div ref={containerRef} className="chart-container" />
          {anchorPos && anchorPos.x > 0 && (
            <span className="chart-anchor-shade" style={{ width: `${Math.min(anchorPos.x, anchorPos.width)}px` }} />
          )}
          {/* Stays mounted (pinned to the edge) while dragged, so it keeps the pointer. */}
          {anchorPos && (draggingAnchor || (anchorPos.x >= 0 && anchorPos.x <= anchorPos.width)) && (
            <div
              className={`chart-anchor-line${draggingAnchor ? " is-dragging" : ""}`}
              style={{ left: `${clamp(anchorPos.x, 0, anchorPos.width)}px` }}
              onPointerDown={startAnchorDrag}
              onPointerMove={onAnchorDragMove}
              onPointerUp={() => setDraggingAnchor(false)}
              onPointerCancel={() => setDraggingAnchor(false)}
              onLostPointerCapture={() => setDraggingAnchor(false)}
            >
              <span className="chart-anchor-label">⟷ {t("zones.anchorLabel")}</span>
            </div>
          )}
          {placingAnchor && <p className="chart-anchor-hint">{t("zones.anchorHint")}</p>}
          {lastPriceY != null && lastPriceX != null && lastCloseRef.current != null && (
            <span className="chart-last-price-line" style={{ top: `${lastPriceY}px`, left: `${lastPriceX}px` }} />
          )}
          {lastPriceY != null && lastCloseRef.current != null && (
            <span className="chart-last-price-badge" style={{ top: `${lastPriceY}px` }}>
              {formatAxisPrice(lastCloseRef.current)}
            </span>
          )}
          {holding && priceLineY != null && (
            <button
              type="button"
              className="chart-position-line-btn"
              style={{ top: `${priceLineY}px`, "--line-color": positionProfit >= 0 ? "var(--up)" : "var(--down)" }}
              onClick={openPositionMenu}
              aria-label={t("chartScan.managePosition")}
            >
              <span className="chart-position-line-label">
                {isShort ? `${t("chartScan.short")} ` : ""}
                {positionQty} •{" "}
                {positionProfit >= 0 ? "+" : ""}
                {formatPrice(positionProfit)}
              </span>
              <span className="chart-position-line-price">{formatPrice(holding.avgPrice)}</span>
            </button>
          )}
          {holding && holding.stopLoss != null && (stopLossY != null || dragLine === "sl") && (
            <div
              className="chart-sltp-line chart-sltp-line-sl"
              style={{ top: `${dragLine === "sl" && dragY != null ? dragY : stopLossY}px` }}
              onPointerDown={(e) => startDragLine(e, "sl")}
              onPointerMove={onDragLineMove}
              onPointerUp={endDragLine}
              onPointerCancel={endDragLine}
            >
              <span className="chart-sltp-line-label">
                {t("portfolio.stopLoss")} · {formatPrice(dragLine === "sl" && dragPrice != null ? dragPrice : holding.stopLoss)}
              </span>
            </div>
          )}
          {holding && holding.takeProfit != null && (takeProfitY != null || dragLine === "tp") && (
            <div
              className="chart-sltp-line chart-sltp-line-tp"
              style={{ top: `${dragLine === "tp" && dragY != null ? dragY : takeProfitY}px` }}
              onPointerDown={(e) => startDragLine(e, "tp")}
              onPointerMove={onDragLineMove}
              onPointerUp={endDragLine}
              onPointerCancel={endDragLine}
            >
              <span className="chart-sltp-line-label">
                {t("portfolio.takeProfit")} · {formatPrice(dragLine === "tp" && dragPrice != null ? dragPrice : holding.takeProfit)}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="chart-scan-topbar">
        <div className="chart-scan-topbar-row">
          <div className="chart-scan-topbar-controls">
            <div className="tf-dropdown" ref={tfRef}>
              <button
                type="button"
                className={`indicators-trigger${tfOpen ? " open" : ""}`}
                onClick={() => setTfOpen((open) => !open)}
                aria-label={currentTf?.label}
                title={currentTf?.label}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7v5l3 2" />
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

            <div className="tf-dropdown zones-controls" ref={zonesMenuRef}>
              <button
                type="button"
                className={`indicators-trigger zones-trigger${zonesOn ? " is-on" : ""}`}
                onClick={toggleZones}
                aria-pressed={zonesOn}
                aria-label={zonesOn ? t("zones.hide") : t("zones.show")}
                title={zonesOn ? t("zones.hide") : t("zones.show")}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="6" rx="1" />
                  <rect x="3" y="14" width="18" height="6" rx="1" />
                </svg>
              </button>
              {zonesOn && (
                <button
                  type="button"
                  className={`indicators-trigger zones-anchor-trigger${
                    placingAnchor ? " open" : anchorTime != null ? " is-on" : ""
                  }`}
                  onClick={toggleAnchor}
                  aria-pressed={placingAnchor || anchorTime != null}
                  aria-label={t(placingAnchor ? "zones.anchorCancel" : anchorTime != null ? "zones.anchorRemove" : "zones.anchorPlace")}
                  title={t(placingAnchor ? "zones.anchorCancel" : anchorTime != null ? "zones.anchorRemove" : "zones.anchorPlace")}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6 3v18" />
                    <path d="M6 4h12l-3 3.5 3 3.5H6" />
                  </svg>
                </button>
              )}
              {zonesOn && (
                <button
                  type="button"
                  className={`indicators-trigger zones-settings-trigger${zonesMenuOpen ? " open" : ""}`}
                  onClick={() => setZonesMenuOpen((open) => !open)}
                  aria-label={t("zones.settings")}
                  aria-expanded={zonesMenuOpen}
                  title={t("zones.settings")}
                >
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <circle cx="12" cy="5" r="2.2" />
                    <circle cx="12" cy="12" r="2.2" />
                    <circle cx="12" cy="19" r="2.2" />
                  </svg>
                </button>
              )}
              {zonesMenuOpen && (
                <div className="tf-dropdown-menu zones-menu">
                  <div className="zones-menu-row">
                    <span>{t("zones.minTouches")}</span>
                    <div className="zones-touch-options">
                      {ZONE_TOUCH_OPTIONS.map((n) => (
                        <button
                          key={n}
                          type="button"
                          className={zonesMinTouches === n ? "active" : ""}
                          onClick={() => setZonesMinTouches(n)}
                        >
                          {n}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="zones-legend">
                    <span>
                      <i className="zones-swatch is-zone" />
                      {t("zones.legendZone")}
                    </span>
                    <span>
                      <i className="zones-swatch is-break" />
                      {t("zones.legendBreakRetest")}
                    </span>
                  </div>
                </div>
              )}
            </div>

            {!TRADING_ENABLED && (
              <button
                type="button"
                className={`indicators-trigger watch-star-btn${saved ? " is-saved" : ""}`}
                onClick={() => watchlist.toggle(symbol, quote?.name)}
                disabled={!saved && !!quoteError}
                aria-pressed={saved}
                aria-label={saved ? t("watchlist.inList") : t("watchlist.addToList")}
                title={saved ? t("watchlist.inList") : t("watchlist.addToList")}
              >
                {saved ? "★" : "☆"}
              </button>
            )}
          </div>
        </div>

        <div className="chart-scan-quote">
          <button type="button" className="chart-scan-symbol-btn" onClick={() => setPickerOpen(true)}>
            <span className="symbol">{symbol}</span>
          </button>
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
      </div>

      {tradeMessage && (
        <div className="chart-scan-status">
          <p
            className={tradeMessage.type === "success" ? "stock-buy-saved" : "disclaimer-note"}
            style={tradeMessage.type === "error" ? { color: "var(--down)" } : undefined}
          >
            {tradeMessage.text}
          </p>
        </div>
      )}

      {TRADING_ENABLED && (
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
            disabled={trading || !quote?.price}
          >
            {t("chartScan.sell")}
          </button>
        </div>
      )}

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

      {positionMenuOpen && holding && (
        <div className="chart-modal-overlay" onClick={() => setPositionMenuOpen(false)}>
          <div className="chart-modal position-menu-modal" onClick={(e) => e.stopPropagation()}>
            <div className="chart-modal-head">
              <div className="chart-modal-title">
                <span className="symbol">{symbol}</span>
              </div>
              <button
                className="chart-modal-close"
                onClick={() => setPositionMenuOpen(false)}
                aria-label={t("common.close")}
              >
                ✕
              </button>
            </div>

            <div className="position-menu-summary">
              <span className={`position-pnl ${positionProfit >= 0 ? "is-up" : "is-down"}`}>
                {positionProfit >= 0 ? "+" : ""}
                {formatPrice(positionProfit)}
              </span>
              <span className="settings-row-sub">
                {isShort ? t("chartScan.short") : t("chartScan.owned")}: {positionQty}
              </span>
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

            <div className="qty-picker-custom">
              <span className="detail-label">{t("chartScan.quantity")}</span>
              <input
                type="number"
                min="1"
                max={positionQty}
                step="1"
                inputMode="numeric"
                className="qty-picker-custom-input"
                value={closeQty}
                onChange={(e) =>
                  setCloseQty(Math.min(positionQty, Math.max(1, parseInt(e.target.value, 10) || 1)))
                }
              />
            </div>

            <div className="position-menu-actions">
              <button
                type="button"
                className="btn btn-ghost btn-rect btn-block"
                onClick={handleReducePosition}
                disabled={trading || !quote?.price}
              >
                {t("chartScan.reducePosition")}
              </button>
              <button
                type="button"
                className="btn btn-danger btn-rect btn-block"
                onClick={handleFullCloseFromMenu}
                disabled={trading || !quote?.price}
              >
                {t("chartScan.closePosition")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
