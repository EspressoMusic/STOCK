import { chart } from "./_yahoo.js";

// GET /api/quote?symbol= on the live site: the chart panel's watchlist prices, today's change
// from the last close.
export default async function handler(req, res) {
  const symbol = String(req.query.symbol || "").toUpperCase();
  if (!symbol) {
    res.status(400).json({ detail: "symbol is needed" });
    return;
  }
  try {
    const { meta } = await chart(symbol, "1d", "1d");
    const price = meta.regularMarketPrice ?? null;
    const prev = meta.chartPreviousClose ?? meta.previousClose ?? null;
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=120");
    res.status(200).json({
      symbol,
      name: meta.shortName || meta.longName || null,
      price,
      change_percent: price != null && prev ? ((price - prev) / prev) * 100 : null,
      market_cap: null,
    });
  } catch (err) {
    res.status(502).json({ detail: `Yahoo: ${err.message}` });
  }
}
