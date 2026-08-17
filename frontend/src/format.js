export function formatPrice(value) {
  if (value === null || value === undefined) return "—";
  return `$${value < 1 ? value.toFixed(4) : value.toFixed(2)}`;
}

export function formatPercent(value) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function formatCompact(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("he-IL", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(unixSeconds) {
  if (!unixSeconds) return null;
  const diffMs = Date.now() - unixSeconds * 1000;
  const diffH = diffMs / 3_600_000;
  if (diffH < 1) return `לפני ${Math.max(1, Math.round(diffH * 60))} דקות`;
  if (diffH < 24) return `לפני ${Math.round(diffH)} שעות`;
  return `לפני ${Math.round(diffH / 24)} ימים`;
}
