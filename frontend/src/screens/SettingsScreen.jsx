import { useEffect, useRef, useState } from "react";
import usePortfolio from "../hooks/usePortfolio";
import usePortfolioDisplay from "../hooks/usePortfolioDisplay";
import useChatHistory from "../hooks/useChatHistory";
import useGoogleAuth from "../hooks/useGoogleAuth";
import { useLang } from "../context/LangContext";
import LegalModal from "../components/LegalModal";
import AccessibilityModal from "../components/AccessibilityModal";
import ChatModal from "../components/ChatModal";

export default function SettingsScreen({ theme, onToggleTheme, reminders, a11y }) {
  const { t, lang, setLang } = useLang();
  const { reset } = usePortfolio();
  const { isAdvanced, toggleMode } = usePortfolioDisplay();
  const { clear } = useChatHistory();
  const { user, ready, configured, renderButton, signOut } = useGoogleAuth();
  const buttonRef = useRef(null);
  const isDark = theme === "dark";
  const [legalDoc, setLegalDoc] = useState(null);
  const [a11yModalOpen, setA11yModalOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    if (ready && !user && buttonRef.current) {
      renderButton(buttonRef.current, { theme: "outline", size: "large", shape: "pill", width: 260 });
    }
  }, [ready, user, renderButton]);

  const remindersLabel = () => {
    if (!reminders.supported) return t("settings.remindersUnsupported");
    if (reminders.permission === "denied") return t("settings.remindersBlocked");
    return reminders.enabled ? t("settings.on") : t("settings.off");
  };

  return (
    <div className="screen">
      <button type="button" className="settings-chat-box" onClick={() => setChatOpen(true)}>
        <div className="settings-chat-box-title">{t("nav.chat")}</div>
        <div className="settings-chat-box-preview">{t("chat.greeting")}</div>
      </button>

      <div className="settings-group">
        {user ? (
          <div className="settings-row">
            {user.picture && <img src={user.picture} alt={user.name} className="account-avatar" />}
            <div className="account-info">
              <div className="account-name">{user.name}</div>
              <div className="account-email">{user.email}</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={signOut}>
              {t("settings.signOut")}
            </button>
          </div>
        ) : configured ? (
          <div className="settings-row">
            <div ref={buttonRef} className="google-btn-slot" />
          </div>
        ) : (
          <div className="settings-row">
            <div className="settings-row-label">Google</div>
            <div className="settings-row-sub">{t("settings.googleNotConfigured")}</div>
          </div>
        )}

        <div className="settings-row">
          <div className="settings-row-label">{t("settings.plan")}</div>
          <span className="badge-pill neutral">{t("settings.free")}</span>
        </div>

        <button type="button" className="settings-row settings-row-btn" onClick={onToggleTheme}>
          <div className="settings-row-label">{t("settings.darkMode")}</div>
          <span className="settings-row-value">{isDark ? t("settings.dark") : t("settings.light")}</span>
        </button>

        <button
          type="button"
          className="settings-row settings-row-btn"
          onClick={() => setLang(lang === "he" ? "en" : "he")}
        >
          <div className="settings-row-label">{t("settings.language")}</div>
          <span className="settings-row-value">{lang === "he" ? "עברית" : "English"}</span>
        </button>

        <button type="button" className="settings-row settings-row-btn" onClick={toggleMode}>
          <div className="settings-row-label">{t("settings.portfolioView")}</div>
          <span className="settings-row-value">{isAdvanced ? t("portfolio.advancedMode") : t("portfolio.simpleMode")}</span>
        </button>

        <button type="button" className="settings-row settings-row-btn" onClick={() => setA11yModalOpen(true)}>
          <div className="settings-row-label">{t("settings.accessibility")}</div>
        </button>

        <button
          type="button"
          className="settings-row settings-row-btn"
          onClick={reminders.toggle}
          disabled={!reminders.supported || reminders.permission === "denied"}
        >
          <div>
            <div className="settings-row-label">{t("settings.reminders")}</div>
            <div className="settings-row-sub">{t("settings.remindersSub")}</div>
          </div>
          <span className="settings-row-value">{remindersLabel()}</span>
        </button>

        <button
          type="button"
          className="settings-row settings-row-btn settings-row-danger"
          onClick={() => {
            if (confirm(t("settings.resetConfirm"))) reset();
          }}
        >
          <div className="settings-row-label">{t("settings.resetPortfolio")}</div>
        </button>

        <button
          type="button"
          className="settings-row settings-row-btn settings-row-danger"
          onClick={() => {
            if (confirm(t("settings.clearConfirm"))) clear();
          }}
        >
          <div className="settings-row-label">{t("settings.clearChat")}</div>
        </button>

        <button type="button" className="settings-row settings-row-btn" onClick={() => setLegalDoc("privacy")}>
          <div className="settings-row-label">{t("settings.privacyPolicy")}</div>
        </button>

        <button type="button" className="settings-row settings-row-btn" onClick={() => setLegalDoc("terms")}>
          <div className="settings-row-label">{t("settings.termsOfUse")}</div>
        </button>
      </div>

      {a11yModalOpen && (
        <AccessibilityModal
          a11y={a11y}
          onClose={() => setA11yModalOpen(false)}
          onOpenStatement={() => {
            setA11yModalOpen(false);
            setLegalDoc("accessibility");
          }}
        />
      )}

      {legalDoc && <LegalModal doc={legalDoc} onClose={() => setLegalDoc(null)} />}

      {chatOpen && <ChatModal onClose={() => setChatOpen(false)} />}
    </div>
  );
}
