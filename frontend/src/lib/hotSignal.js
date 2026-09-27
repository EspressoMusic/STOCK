// A stock is only flagged "hot" when there's a concrete, checkable reason —
// not just because it's already on an extreme-movers list (every row there
// has a big % change by definition, so that alone says nothing special).
const RELATIVE_VOLUME_THRESHOLD = 10; // today's volume vs. its own 3-month average
const NEWS_BUZZ_WINDOW_HOURS = 6; // published recently enough to be "breaking"

export function getHotSignal(stock) {
  if (!stock) return { isHot: false, reason: null };

  const relativeVolume =
    stock.volume && stock.avg_volume ? stock.volume / stock.avg_volume : null;
  if (relativeVolume !== null && relativeVolume >= RELATIVE_VOLUME_THRESHOLD) {
    return { isHot: true, reason: "volume", relativeVolume };
  }

  const cutoff = Date.now() / 1000 - NEWS_BUZZ_WINDOW_HOURS * 3600;
  const freshNews = (stock.news || []).find((n) => n.published_at && n.published_at >= cutoff);
  if (freshNews) {
    return { isHot: true, reason: "news", newsItem: freshNews };
  }

  return { isHot: false, reason: null };
}
