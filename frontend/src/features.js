// Demo trading (buy/sell, positions, SL/TP, the portfolio tab) is switched off
// for now — the app is view-only and the center tab shows the saved watchlist.
// Flip this back to true to bring the portfolio and trade buttons back.
export const TRADING_ENABLED = false;

// The middle bottom-nav tab, which is also the app's home screen.
export const CENTER_TAB = TRADING_ENABLED ? "portfolio" : "watchlist";
