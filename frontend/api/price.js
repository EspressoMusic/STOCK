import { chart } from "./_yahoo.js";

// GET /api/price?symbol= on the live site: the chart panel's live price (its last candle and
// price line), asked every few seconds, as backend/app/quote.py's get_live_price.
export default async function handler(req, res) {
  const symbol = String(req.query.symbol || "").toUpperCase();
  if (!symbol) {
    res.status(400).json({ detail: "symbol is needed" });
    return;
  }
  try {
    const { meta } = await chart(symbol, "1m", "1d");
    const price = meta.regularMarketPrice;
    if (price == null) throw new Error("no price");
    res.setHeader("Cache-Control", "s-maxage=2, stale-while-revalidate=10");
    res.status(200).json({ symbol, price });
  } catch (err) {
    res.status(502).json({ detail: `Yahoo: ${err.message}` });
  }
}
