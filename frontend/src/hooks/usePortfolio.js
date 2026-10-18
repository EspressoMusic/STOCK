import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "demo-portfolio-v1";
const STARTING_CASH = 100000;

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { cash: STARTING_CASH, holdings: [], transactions: [] };
    const parsed = JSON.parse(raw);
    return {
      cash: typeof parsed.cash === "number" ? parsed.cash : STARTING_CASH,
      holdings: Array.isArray(parsed.holdings) ? parsed.holdings : [],
      transactions: Array.isArray(parsed.transactions) ? parsed.transactions : [],
    };
  } catch {
    return { cash: STARTING_CASH, holdings: [], transactions: [] };
  }
}

function makeTransaction(symbol, side, shares, price, pnl = null) {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    symbol,
    side,
    shares,
    price,
    pnl,
    timestamp: new Date().toISOString(),
  };
}

// Demo-only, client-side "portfolio": starting play cash + a list of
// {symbol, name, shares, avgPrice} holdings. shares can be negative, which
// represents a short position (sold borrowed shares, buy back later to
// close it out). Nothing here is real money — it's persisted to
// localStorage only, there's no backend account behind it.
export default function usePortfolio() {
  const [state, setState] = useState(load);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [state]);

  // Buying always moves a position toward/into "long": it covers an
  // existing short first, and any leftover quantity opens/adds to a long.
  const buy = useCallback((symbol, name, shares, price) => {
    setState((prev) => {
      if (shares <= 0) return prev;
      const cost = shares * price;
      if (cost > prev.cash) return prev;
      const existing = prev.holdings.find((h) => h.symbol === symbol);
      let holdings;
      let pnl = null;
      if (!existing) {
        holdings = [...prev.holdings, { symbol, name, shares, avgPrice: price, stopLoss: null, takeProfit: null }];
      } else if (existing.shares >= 0) {
        const totalShares = existing.shares + shares;
        const avgPrice = (existing.shares * existing.avgPrice + shares * price) / totalShares;
        holdings = prev.holdings.map((h) =>
          h.symbol === symbol ? { ...h, shares: totalShares, avgPrice } : h
        );
      } else {
        const closedQty = Math.min(shares, -existing.shares);
        pnl = (existing.avgPrice - price) * closedQty;
        const newShares = existing.shares + shares;
        if (newShares < -0.0001) {
          holdings = prev.holdings.map((h) => (h.symbol === symbol ? { ...h, shares: newShares } : h));
        } else if (newShares > 0.0001) {
          // Fully covered the short; the remainder opens a fresh long lot, so any
          // stop/target set for the old short position no longer applies.
          holdings = prev.holdings.map((h) =>
            h.symbol === symbol
              ? { ...h, shares: newShares, avgPrice: price, name: name || h.name, stopLoss: null, takeProfit: null }
              : h
          );
        } else {
          holdings = prev.holdings.filter((h) => h.symbol !== symbol);
        }
      }
      const transactions = [...prev.transactions, makeTransaction(symbol, "buy", shares, price, pnl)];
      return { cash: prev.cash - cost, holdings, transactions };
    });
  }, []);

  // Selling always moves a position toward/into "short": it reduces an
  // existing long first, and any leftover quantity opens/adds to a short.
  const sell = useCallback((symbol, name, shares, price) => {
    setState((prev) => {
      if (shares <= 0) return prev;
      const proceeds = shares * price;
      const existing = prev.holdings.find((h) => h.symbol === symbol);
      let holdings;
      let pnl = null;
      if (!existing) {
        holdings = [...prev.holdings, { symbol, name, shares: -shares, avgPrice: price, stopLoss: null, takeProfit: null }];
      } else if (existing.shares <= 0) {
        const shortQty = -existing.shares + shares;
        const avgPrice = (-existing.shares * existing.avgPrice + shares * price) / shortQty;
        holdings = prev.holdings.map((h) =>
          h.symbol === symbol ? { ...h, shares: -shortQty, avgPrice } : h
        );
      } else {
        const closedQty = Math.min(shares, existing.shares);
        pnl = (price - existing.avgPrice) * closedQty;
        const newShares = existing.shares - shares;
        if (newShares > 0.0001) {
          holdings = prev.holdings.map((h) => (h.symbol === symbol ? { ...h, shares: newShares } : h));
        } else if (newShares < -0.0001) {
          // Fully closed the long; the remainder opens a fresh short lot, so any
          // stop/target set for the old long position no longer applies.
          holdings = prev.holdings.map((h) =>
            h.symbol === symbol
              ? { ...h, shares: newShares, avgPrice: price, name: name || h.name, stopLoss: null, takeProfit: null }
              : h
          );
        } else {
          holdings = prev.holdings.filter((h) => h.symbol !== symbol);
        }
      }
      const transactions = [...prev.transactions, makeTransaction(symbol, "sell", shares, price, pnl)];
      return { cash: prev.cash + proceeds, holdings, transactions };
    });
  }, []);

  // Closes a position outright, regardless of direction (long or short).
  const closePosition = useCallback((symbol, price) => {
    setState((prev) => {
      const existing = prev.holdings.find((h) => h.symbol === symbol);
      if (!existing) return prev;
      const proceeds = existing.shares * price;
      const holdings = prev.holdings.filter((h) => h.symbol !== symbol);
      const side = existing.shares > 0 ? "sell" : "buy";
      const pnl =
        existing.shares > 0
          ? (price - existing.avgPrice) * existing.shares
          : (existing.avgPrice - price) * Math.abs(existing.shares);
      const transactions = [
        ...prev.transactions,
        makeTransaction(symbol, side, Math.abs(existing.shares), price, pnl),
      ];
      return { cash: prev.cash + proceeds, holdings, transactions };
    });
  }, []);

  const removeHolding = useCallback((symbol) => {
    setState((prev) => ({ ...prev, holdings: prev.holdings.filter((h) => h.symbol !== symbol) }));
  }, []);

  // Sets/clears the auto-close levels checked by the stop-loss/take-profit
  // monitor in PortfolioScreen. Pass null for either side to clear it.
  const setStopLossTakeProfit = useCallback((symbol, { stopLoss, takeProfit }) => {
    setState((prev) => ({
      ...prev,
      holdings: prev.holdings.map((h) =>
        h.symbol === symbol ? { ...h, stopLoss: stopLoss ?? null, takeProfit: takeProfit ?? null } : h
      ),
    }));
  }, []);

  const reset = useCallback(() => {
    setState({ cash: STARTING_CASH, holdings: [], transactions: [] });
  }, []);

  return {
    cash: state.cash,
    holdings: state.holdings,
    transactions: state.transactions,
    buy,
    sell,
    closePosition,
    removeHolding,
    setStopLossTakeProfit,
    reset,
  };
}
