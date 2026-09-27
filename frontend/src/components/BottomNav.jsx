import { useNav } from "../context/NavContext";
import { useLang } from "../context/LangContext";

const TAB_IDS = ["chart-scan", "portfolio", "interesting"];
const CENTER_ID = "portfolio";

const LABEL_KEYS = {
  portfolio: "nav.portfolio",
  "chart-scan": "nav.chartScan",
  interesting: "nav.interesting",
};

const ICONS = {
  portfolio: <path d="M4 8a1 1 0 0 1 1-1h2.2l1-1.6A1 1 0 0 1 9.05 5h5.9a1 1 0 0 1 .85.4L16.8 7H19a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8Z" />,
  "chart-scan": <path d="M3 3v18h18M7 15l4-6 3 4 5-8" />,
  interesting: (
    <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
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
