export function formatPrice(value) {
  if (value === null || value === undefined) return "—";
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatRate(value) {
  if (value === null || value === undefined) return "—";
  return value.toFixed(5);
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

export function formatDateTime(iso, lang = "he") {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(lang === "en" ? "en-GB" : "he-IL", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(unixSeconds, lang = "he") {
  if (!unixSeconds) return null;
  const diffMs = Date.now() - unixSeconds * 1000;
  const diffH = diffMs / 3_600_000;
  const isEn = lang === "en";
  if (diffH < 1) {
    const mins = Math.max(1, Math.round(diffH * 60));
    return isEn ? `${mins}m ago` : `לפני ${mins} דקות`;
  }
  if (diffH < 24) {
    const hrs = Math.round(diffH);
    return isEn ? `${hrs}h ago` : `לפני ${hrs} שעות`;
  }
  const days = Math.round(diffH / 24);
  return isEn ? `${days}d ago` : `לפני ${days} ימים`;
}
