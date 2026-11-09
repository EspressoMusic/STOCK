import { useNav } from "../context/NavContext";
import { CENTER_TAB } from "../features";
import { useLang } from "../context/LangContext";

const TAB_IDS = ["signals", "chart-scan", CENTER_TAB, "interesting"];
const CENTER_ID = CENTER_TAB;

const LABEL_KEYS = {
  portfolio: "nav.portfolio",
  watchlist: "nav.watchlist",
  "chart-scan": "nav.chartScan",
  interesting: "nav.interesting",
  signals: "nav.signals",
};

const ICONS = {
  portfolio: <path d="M4 8a1 1 0 0 1 1-1h2.2l1-1.6A1 1 0 0 1 9.05 5h5.9a1 1 0 0 1 .85.4L16.8 7H19a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8Z" />,
  watchlist: <path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z" />,
  "chart-scan": <path d="M3 3v18h18M7 15l4-6 3 4 5-8" />,
  interesting: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </>
  ),
  signals: (
    <>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </>
  ),
};

export default function BottomNav() {
  const { tab, goTo } = useNav();
  const { t } = useLang();

  return (
    <nav className="bottom-nav">
      {TAB_IDS.map((id) => {
        const isCenter = id === CENTER_ID;
        return (
          <button
            key={id}
            className={`bottom-nav-btn ${isCenter ? "bottom-nav-btn-center" : ""} ${tab === id ? "active" : ""}`}
            onClick={() => goTo(id)}
          >
            <span className={`bottom-nav-icon-wrap ${isCenter ? "bottom-nav-icon-wrap-center" : ""}`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {ICONS[id]}
              </svg>
            </span>
            {t(LABEL_KEYS[id])}
          </button>
        );
      })}
    </nav>
  );
}
