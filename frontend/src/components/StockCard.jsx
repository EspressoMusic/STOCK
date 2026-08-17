import { useState } from "react";
import { formatPrice, formatPercent, formatCompact, formatRelative } from "../format";

export default function StockCard({ stock, direction }) {
  const [open, setOpen] = useState(false);
  const isBroken = direction === "broken";
  const changeClass = isBroken ? "chg-broken" : direction === "losers" ? "chg-down" : "chg-up";
  const badgeValue = isBroken ? stock.fifty_two_week_change_percent : stock.change_percent;

  return (
    <div className={`card ${open ? "card-open" : ""}`}>
      <button className="card-head" onClick={() => setOpen((o) => !o)}>
        <div className="card-head-main">
          <div className="symbol-row">
            <span className="symbol">{stock.symbol}</span>
            <span className={`chg-badge ${changeClass}`}>{formatPercent(badgeValue)}</span>
            {isBroken && <span className="badge-caption">12 חודשים</span>}
          </div>
          <div className="name">{stock.name || "—"}</div>
        </div>
        <div className="card-head-side">
          <div className="price">{formatPrice(stock.price)}</div>
          {stock.sector && <div className="tag">{stock.sector}</div>}
        </div>
      </button>

      {stock.ai_summary && (
        <div className="ai-bubble">
          <span className="ai-label">תחושת בטן</span>
          <p>{stock.ai_summary}</p>
        </div>
      )}

      {open && (
        <div className="card-detail">
          <div className="detail-grid">
            {isBroken && (
              <div className="detail-item">
                <span className="detail-label">שינוי היום</span>
                <span>{formatPercent(stock.change_percent)}</span>
              </div>
            )}
            <div className="detail-item">
              <span className="detail-label">נפח מסחר</span>
              <span>{formatCompact(stock.volume)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">שווי שוק</span>
              <span>{stock.market_cap ? `$${formatCompact(stock.market_cap)}` : "—"}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">שיא 52 שבועות</span>
              <span>{formatPrice(stock.fifty_two_week_high)}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">שפל 52 שבועות</span>
              <span>{formatPrice(stock.fifty_two_week_low)}</span>
            </div>
          </div>

          <div className="analyst-block">
            <span className="detail-label">יעד אנליסטים</span>
            {stock.target_mean_price ? (
              <p>
                ממוצע {formatPrice(stock.target_mean_price)} (טווח {formatPrice(stock.target_low_price)}–
                {formatPrice(stock.target_high_price)}), {stock.num_analyst_opinions || 0} אנליסטים
                {stock.recommendation_key ? ` · המלצה: ${stock.recommendation_key}` : ""}
              </p>
            ) : (
              <p className="muted">אין כיסוי אנליסטים זמין למניה הזו.</p>
            )}
          </div>

          <div className="news-block">
            <span className="detail-label">חדשות אחרונות</span>
            {stock.news && stock.news.length > 0 ? (
              <ul>
                {stock.news.map((n, i) => (
                  <li key={i}>
                    {n.link ? (
                      <a href={n.link} target="_blank" rel="noreferrer">
                        {n.title}
                      </a>
                    ) : (
                      n.title
                    )}
                    <span className="news-meta">
                      {n.publisher}
                      {formatRelative(n.published_at) ? ` · ${formatRelative(n.published_at)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">אין חדשות זמינות לאחרונה.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
