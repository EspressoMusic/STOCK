const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8001";

async function request(path, options) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function getStatus() {
  return request("/api/status");
}

export function getSectors() {
  return request("/api/sectors");
}

export function getLatestScan({ direction, category }) {
  const params = new URLSearchParams({ direction });
  if (category) params.set("category", category);
  return request(`/api/scans/latest?${params.toString()}`);
}

export function runScan({ direction, sector, maxPrice, minAbsPercent }) {
  return request("/api/scans/run", {
    method: "POST",
    body: JSON.stringify({
      direction,
      sector: sector || null,
      max_price: maxPrice || null,
      min_abs_percent: minAbsPercent || null,
    }),
  });
}

export function getLatestTechnicalScan(timeframe = "15m") {
  return request(`/api/technical-scan/latest?timeframe=${timeframe}`);
}

export function runTechnicalScan(timeframe = "15m") {
  return request(`/api/technical-scan/run?timeframe=${timeframe}`, { method: "POST" });
}

export function getLatestEmaBreakoutScan() {
  return request("/api/ema-breakout-scan/latest");
}

export function runEmaBreakoutScan() {
  return request("/api/ema-breakout-scan/run", { method: "POST" });
}

export function getLatestEmaTouch4hScan() {
  return request("/api/ema-touch-4h-scan/latest");
}

export function runEmaTouch4hScan() {
  return request("/api/ema-touch-4h-scan/run", { method: "POST" });
}

export function getLatestEmaDoubleTouchForexScan() {
  return request("/api/ema-double-touch-forex-scan/latest");
}

export function runEmaDoubleTouchForexScan() {
  return request("/api/ema-double-touch-forex-scan/run", { method: "POST" });
}

export function getCandles(symbol, timeframe = "1h") {
  return request(`/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`);
}

export function getQuote(symbol) {
  return request(`/api/quote?symbol=${encodeURIComponent(symbol)}`);
}

export function searchSymbols(query) {
  return request(`/api/symbol-search?q=${encodeURIComponent(query)}`);
}

export function getCryptoScan() {
  return request("/api/crypto-scan");
}

export function getCapScan(size) {
  return request(`/api/cap-scan?size=${size}`);
}

export function getWorldNews(limit = 4) {
  return request(`/api/world-news?limit=${limit}`);
}

export function getNewsDigest(symbol, news) {
  return request("/api/news-digest", {
    method: "POST",
    body: JSON.stringify({
      symbol,
      news: news.map((n) => ({ title: n.title, publisher: n.publisher })),
    }),
  });
}

async function requestForm(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: formData });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function analyzeChart(imageFile) {
  const form = new FormData();
  form.append("image", imageFile);
  return requestForm("/api/chart-analysis", form);
}

export function sendChatMessage(messages, imageFile) {
  const form = new FormData();
  form.append("messages", JSON.stringify(messages));
  if (imageFile) form.append("image", imageFile);
  return requestForm("/api/chat", form);
}
