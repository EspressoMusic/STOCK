import { useEffect, useState } from "react";
import BottomNav from "./components/BottomNav";
import PortfolioScreen from "./screens/PortfolioScreen";
import ChartScanScreen from "./screens/ChartScanScreen";
import InterestingScreen from "./screens/InterestingScreen";
import SettingsScreen from "./screens/SettingsScreen";
import { LangProvider, useLang } from "./context/LangContext";
import { NavProvider, useNav } from "./context/NavContext";
import useReminders from "./hooks/useReminders";
import useAccessibility from "./hooks/useAccessibility";

function getInitialTheme() {
  const stored = localStorage.getItem("theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function AppShell() {
  const { tab, goTo } = useNav();
  const { t } = useLang();
  const [theme, setTheme] = useState(getInitialTheme);
  const reminders = useReminders(t);
  const a11y = useAccessibility();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  return (
    <div className="phone-shell">
      {tab === "portfolio" && (
        <div className="top-bar">
          <button
            type="button"
            className={`top-bar-settings-btn ${tab === "settings" ? "active" : ""}`}
            onClick={() => goTo("settings")}
            aria-label={t("nav.settings")}
          >
            <svg viewBox="-0.2 -1.6 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 8.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8z" />
              <path d="M19.4 13.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2.06 2.06 0 1 1-2.92 2.92l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56v.17a2.06 2.06 0 1 1-4.12 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2.06 2.06 0 1 1-2.92-2.92l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03h-.17a2.06 2.06 0 1 1 0-4.12h.09a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2.06 2.06 0 1 1 2.92-2.92l.06.06a1.7 1.7 0 0 0 1.87.34h.08a1.7 1.7 0 0 0 1.03-1.56v-.17a2.06 2.06 0 1 1 4.12 0v.09a1.7 1.7 0 0 0 1.03 1.56h.08a1.7 1.7 0 0 0 1.87-.34l.06-.06a2.06 2.06 0 1 1 2.92 2.92l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08a1.7 1.7 0 0 0 1.56 1.03h.17a2.06 2.06 0 1 1 0 4.12h-.09a1.7 1.7 0 0 0-1.56 1.03z" />
            </svg>
          </button>
        </div>
      )}

      <div className="screen-stage" key={tab}>
        {tab === "portfolio" && <PortfolioScreen />}
        {tab === "chart-scan" && <ChartScanScreen />}
        {tab === "interesting" && <InterestingScreen />}
        {tab === "settings" && (
          <SettingsScreen
            theme={theme}
            onToggleTheme={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
            reminders={reminders}
            a11y={a11y}
          />
        )}
      </div>

      <BottomNav />
    </div>
  );
}

export default function App() {
  return (
    <LangProvider>
      <NavProvider>
        <AppShell />
      </NavProvider>
    </LangProvider>
  );
}
