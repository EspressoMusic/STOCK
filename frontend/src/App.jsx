import { useEffect, useState } from "react";
import BottomNav from "./components/BottomNav";
import WelcomeGate from "./components/WelcomeGate";
import PortfolioScreen from "./screens/PortfolioScreen";
import ChartScanScreen from "./screens/ChartScanScreen";
import InterestingScreen from "./screens/InterestingScreen";
import WatchlistScreen from "./screens/WatchlistScreen";
import SettingsScreen from "./screens/SettingsScreen";
import { LangProvider, useLang } from "./context/LangContext";
import { NavProvider, useNav } from "./context/NavContext";
import useReminders from "./hooks/useReminders";
import useAccessibility from "./hooks/useAccessibility";
import useAuth from "./hooks/useAuth";
import { CENTER_TAB } from "./features";

function AppShell({ auth }) {
  const { tab, goTo } = useNav();
  const { t } = useLang();
  // Always open in light mode with the default skin (MT5 is always dark);
  // both toggles only apply to the current visit.
  const [theme, setTheme] = useState("light");
  const [skin, setSkin] = useState("default");
  const reminders = useReminders(t);
  const a11y = useAccessibility();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute("data-skin", skin);
  }, [skin]);

  return (
    <div className="phone-shell">
      {tab === CENTER_TAB && (
        <div className="top-bar">
          <button
            type="button"
            className={`top-bar-settings-btn ${tab === "settings" ? "active" : ""}`}
            onClick={() => goTo("settings")}
            aria-label={t("nav.settings")}
            title={t("nav.settings")}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        </div>
      )}

      <div className="screen-stage" key={tab}>
        {tab === "portfolio" && <PortfolioScreen skin={skin} />}
        {tab === "watchlist" && <WatchlistScreen />}
        {tab === "chart-scan" && <ChartScanScreen />}
        {tab === "interesting" && <InterestingScreen />}
        {tab === "settings" && (
          <SettingsScreen
            theme={theme}
            onToggleTheme={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
            skin={skin}
            onToggleSkin={() => setSkin((prev) => (prev === "mt5" ? "default" : "mt5"))}
            reminders={reminders}
            a11y={a11y}
            auth={auth}
          />
        )}
      </div>

      <BottomNav />
    </div>
  );
}

function AuthGate() {
  const auth = useAuth();
  if (!auth.user) return <WelcomeGate auth={auth} />;
  return <AppShell auth={auth} />;
}

export default function App() {
  return (
    <LangProvider>
      <NavProvider>
        <AuthGate />
      </NavProvider>
    </LangProvider>
  );
}
