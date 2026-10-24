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

export function getBillingStatus() {
  return request("/api/billing/status");
}

export function authWithGoogle(credential) {
  return request("/api/auth/google", {
    method: "POST",
    body: JSON.stringify({ credential }),
  });
}

export function authAsGuest() {
  return request("/api/auth/guest", { method: "POST" });
}

export function getCurrentUser(token) {
  return request("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
}

export function authLogout(token) {
  return request("/api/auth/logout", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
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

export function getLatestPatternStrategyScan() {
  return request("/api/pattern-strategy-scan/latest");
}

export function runPatternStrategyScan() {
  return request("/api/pattern-strategy-scan/run", { method: "POST" });
}

export function getLatestTripleTouchScan(market) {
  return request(`/api/triple-touch-scan/latest?market=${market}`);
}

export function runTripleTouchScan(market) {
  return request(`/api/triple-touch-scan/run?market=${market}`, { method: "POST" });
}

export function getLatestStochExtremeScan() {
  return request("/api/stoch-extreme-scan/latest");
}

export function runStochExtremeScan() {
  return request("/api/stoch-extreme-scan/run", { method: "POST" });
}

export function getLatestZoneTouchScan() {
  return request("/api/zone-touch-scan/latest");
}

export function runZoneTouchScan() {
  return request("/api/zone-touch-scan/run", { method: "POST" });
}

export function getCandles(symbol, timeframe = "1h") {
  return request(`/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`);
}

// Zone trainer charts (trainer.html). Marks (see lib/zoneTraining.js) and candles are
// camelCase objects here; on the wire marks are snake_case and candles are
// [time, open, high, low, close] rows.
function trainingChartFromApi(c) {
  return {
    id: c.id,
    symbol: c.symbol,
    timeframe: c.timeframe,
    note: c.note,
    params: c.params,
    updatedAt: c.updated_at,
    marks: c.marks.map((m) => ({
      id: m.id,
      kind: m.kind,
      source: m.source,
      verdict: m.verdict,
      fromTime: m.from_time,
      toTime: m.to_time,
      top: m.top,
      bottom: m.bottom,
      dir: m.dir,
      algoVersion: m.algo_version,
      note: m.note ?? "",
    })),
    candles: c.candles.map(([time, open, high, low, close]) => ({ time, open, high, low, close })),
  };
}

function trainingChartToApi({ symbol, timeframe, marks, note, params, candles }) {
  return JSON.stringify({
    symbol,
    timeframe,
    note,
    params,
    marks: marks.map((m) => ({
      id: m.id,
      kind: m.kind,
      source: m.source,
      verdict: m.verdict,
      from_time: m.fromTime,
      to_time: m.toTime,
      top: m.top,
      bottom: m.bottom,
      dir: m.dir ?? null,
      algo_version: m.algoVersion ?? null,
      note: m.note ?? "",
    })),
    candles: candles.map((c) => [c.time, c.open, c.high, c.low, c.close]),
  });
}

export function listZoneTraining() {
  return request("/api/zone-training").then((rows) => rows.map(trainingChartFromApi));
}

export function createZoneTraining(chart) {
  return request("/api/zone-training", { method: "POST", body: trainingChartToApi(chart) }).then(trainingChartFromApi);
}

export function updateZoneTraining(id, chart) {
  return request(`/api/zone-training/${id}`, { method: "PUT", body: trainingChartToApi(chart) }).then(
    trainingChartFromApi
  );
}

export function deleteZoneTraining(id) {
  return request(`/api/zone-training/${id}`, { method: "DELETE" });
}

export function getQuote(symbol) {
  return request(`/api/quote?symbol=${encodeURIComponent(symbol)}`);
}

export function getStockProfile(symbol) {
  return request(`/api/stock-profile?symbol=${encodeURIComponent(symbol)}`);
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

export function getNewsDigest(symbol, news) {
  return request("/api/news-digest", {
    method: "POST",
    body: JSON.stringify({
      symbol,
      news: news.map((n) => ({ title: n.title, publisher: n.publisher })),
    }),
  });
}

export function getDailyDigest({ cash, holdings }) {
  return request("/api/daily-digest", {
    method: "POST",
    body: JSON.stringify({
      cash,
      holdings: holdings.map((h) => ({
        symbol: h.symbol,
        shares: h.shares,
        avg_price: h.avgPrice,
        price: h.price ?? null,
        change_percent: h.changePercent ?? null,
      })),
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
