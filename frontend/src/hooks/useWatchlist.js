import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "watchlist-v1";
const PORTFOLIO_KEY = "demo-portfolio-v1";

// The first time this runs, the list is seeded from the demo portfolio's
// holdings, so stocks that were "saved to portfolio" before trading was
// switched off (see features.js) carry over into the watchlist.
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    }
    const portfolio = JSON.parse(localStorage.getItem(PORTFOLIO_KEY) || "null");
    const holdings = Array.isArray(portfolio?.holdings) ? portfolio.holdings : [];
    const seeded = holdings.map((h) => ({ symbol: h.symbol, name: h.name || null, addedAt: new Date().toISOString() }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(seeded));
    return seeded;
  } catch {
    return [];
  }
}

// One module-level store shared by every component that calls the hook, so a
// star toggled in a modal shows up in the list immediately.
let items = load();
const listeners = new Set();

function setItems(next) {
  items = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    /* storage full or blocked — keep the in-memory list */
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return items;
}

// Client-side list of stocks the user chose to keep an eye on: {symbol, name, addedAt}.
// Newest first. Persisted to localStorage only.
export default function useWatchlist() {
  const list = useSyncExternalStore(subscribe, getSnapshot);

  const isSaved = useCallback((symbol) => list.some((s) => s.symbol === symbol), [list]);

  const add = useCallback((symbol, name) => {
    if (!symbol || items.some((s) => s.symbol === symbol)) return;
    setItems([{ symbol, name: name || null, addedAt: new Date().toISOString() }, ...items]);
  }, []);

  const remove = useCallback((symbol) => {
    setItems(items.filter((s) => s.symbol !== symbol));
  }, []);

  const toggle = useCallback(
    (symbol, name) => {
      if (items.some((s) => s.symbol === symbol)) remove(symbol);
      else add(symbol, name);
    },
    [add, remove]
  );

  return { items: list, isSaved, add, remove, toggle };
}
