import { useEffect, useState } from "react";
import { getWorldNews } from "../api";
import { useLang } from "../context/LangContext";

export default function WorldNewsPanel() {
  const { t } = useLang();
  const [news, setNews] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    getWorldNews(4)
      .then((res) => setNews(res.news))
      .catch(() => setError(true));
  }, []);

  return (
    <section className="world-news-panel">
      <h2 className="world-news-title">{t("worldNews.title")}</h2>

      {error && <p className="disclaimer-note">{t("worldNews.error")}</p>}
      {!error && news === null && <p className="disclaimer-note">{t("worldNews.loading")}</p>}
      {!error && news?.length === 0 && <p className="disclaimer-note">{t("worldNews.empty")}</p>}

      {!error && news?.length > 0 && (
        <ul className="world-news-list">
          {news.map((item, i) => {
            const content = (
              <>
                <span className="world-news-item-title">{item.title}</span>
                {item.impact && <span className="world-news-item-impact">{item.impact}</span>}
                {item.publisher && <span className="world-news-item-publisher">{item.publisher}</span>}
              </>
            );
            return (
              <li key={i} className="world-news-item">
                {item.link ? (
                  <a className="world-news-item-body" href={item.link} target="_blank" rel="noopener noreferrer">
                    {content}
                  </a>
                ) : (
                  <div className="world-news-item-body">{content}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
