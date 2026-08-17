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
