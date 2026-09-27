import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "demo-portfolio-v1";
const STARTING_CASH = 100000;

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { cash: STARTING_CASH, holdings: [] };
    const parsed = JSON.parse(raw);
    return {
      cash: typeof parsed.cash === "number" ? parsed.cash : STARTING_CASH,
      holdings: Array.isArray(parsed.holdings) ? parsed.holdings : [],
    };
  } catch {
    return { cash: STARTING_CASH, holdings: [] };
  }
}

// Demo-only, client-side "portfolio": starting play cash + a list of
// {symbol, name, shares, avgPrice} holdings. Nothing here is real money —
// it's persisted to localStorage only, there's no backend account behind it.
export default function usePortfolio() {
  const [state, setState] = useState(load);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [state]);

  const buy = useCallback((symbol, name, shares, price) => {
    setState((prev) => {
      const cost = shares * price;
      if (cost > prev.cash || shares <= 0) return prev;
      const existing = prev.holdings.find((h) => h.symbol === symbol);
      let holdings;
      if (existing) {
        const totalShares = existing.shares + shares;
        const avgPrice = (existing.shares * existing.avgPrice + shares * price) / totalShares;
        holdings = prev.holdings.map((h) =>
          h.symbol === symbol ? { ...h, shares: totalShares, avgPrice } : h
        );
      } else {
        holdings = [...prev.holdings, { symbol, name, shares, avgPrice: price }];
      }
      return { cash: prev.cash - cost, holdings };
    });
  }, []);

  const sell = useCallback((symbol, shares, price) => {
    setState((prev) => {
      const existing = prev.holdings.find((h) => h.symbol === symbol);
      if (!existing) return prev;
      const sellShares = Math.min(shares, existing.shares);
      const proceeds = sellShares * price;
      const remaining = existing.shares - sellShares;
      const holdings =
        remaining > 0.0001
          ? prev.holdings.map((h) => (h.symbol === symbol ? { ...h, shares: remaining } : h))
          : prev.holdings.filter((h) => h.symbol !== symbol);
      return { cash: prev.cash + proceeds, holdings };
    });
  }, []);

  const removeHolding = useCallback((symbol) => {
    setState((prev) => ({ ...prev, holdings: prev.holdings.filter((h) => h.symbol !== symbol) }));
  }, []);

  const reset = useCallback(() => {
    setState({ cash: STARTING_CASH, holdings: [] });
  }, []);

  return { cash: state.cash, holdings: state.holdings, buy, sell, removeHolding, reset };
}
