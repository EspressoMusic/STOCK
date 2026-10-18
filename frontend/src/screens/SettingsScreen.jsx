import { useEffect, useRef, useState } from "react";
import usePortfolio from "../hooks/usePortfolio";
import usePortfolioDisplay from "../hooks/usePortfolioDisplay";
import useTechScanSettings from "../hooks/useTechScanSettings";
import useBilling from "../hooks/useBilling";
import { useLang } from "../context/LangContext";
import LegalModal from "../components/LegalModal";
import AccessibilityModal from "../components/AccessibilityModal";
import { TRADING_ENABLED } from "../features";

export default function SettingsScreen({ theme, onToggleTheme, skin, onToggleSkin, reminders, a11y, auth }) {
  const { t, lang, setLang } = useLang();
  const { reset } = usePortfolio();
  const { isAdvanced, toggleMode } = usePortfolioDisplay();
  const { extraScansEnabled, toggleExtraScans } = useTechScanSettings();
  const { user, googleReady, googleConfigured, renderGoogleButton, signOut } = auth;
  const isGuest = user?.kind === "guest";
  const { plan, canCheckout, openCheckout } = useBilling();
  const buttonRef = useRef(null);
  const isDark = theme === "dark";
  const [legalDoc, setLegalDoc] = useState(null);
  const [a11yModalOpen, setA11yModalOpen] = useState(false);

  useEffect(() => {
    if (googleReady && isGuest && buttonRef.current) {
      renderGoogleButton(buttonRef.current, { theme: "outline", size: "large", shape: "pill", width: 260 });
    }
  }, [googleReady, isGuest, renderGoogleButton]);

  const remindersLabel = () => {
    if (!reminders.supported) return t("settings.remindersUnsupported");
    if (reminders.permission === "denied") return t("settings.remindersBlocked");
    return reminders.enabled ? t("settings.on") : t("settings.off");
  };

  return (
    <div className="screen">
      <div className="settings-group">
        {!isGuest ? (
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
        ) : googleConfigured ? (
          <div className="settings-row">
            <div>
              <div className="settings-row-label">{t("settings.guestAccount")}</div>
              <div className="settings-row-sub">{t("settings.guestAccountSub")}</div>
            </div>
            <div ref={buttonRef} className="google-btn-slot" />
          </div>
        ) : (
          <div className="settings-row">
            <div className="settings-row-label">Google</div>
            <div className="settings-row-sub">{t("settings.googleNotConfigured")}</div>
          </div>
        )}

        <div className="settings-row">
          <div>
            <div className="settings-row-label">{t("settings.plan")}</div>
            <div className="settings-row-sub">{plan === "pro" ? t("settings.planDescPro") : t("settings.planDesc")}</div>
          </div>
          <span className={`badge-pill ${plan === "pro" ? "bullish" : "neutral"}`}>
            {plan === "pro" ? t("settings.pro") : t("settings.free")}
          </span>
        </div>

        {plan !== "pro" && (
          <button
            type="button"
            className="settings-row settings-row-btn"
            onClick={openCheckout}
            disabled={!canCheckout}
          >
            <div>
              <div className="settings-row-label">{t("settings.upgrade")}</div>
              <div className="settings-row-sub">
                {canCheckout ? t("settings.upgradeSub") : t("settings.upgradeUnavailable")}
              </div>
            </div>
          </button>
        )}

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

        <button type="button" className="settings-row settings-row-btn" onClick={onToggleSkin}>
          <div>
            <div className="settings-row-label">{t("settings.mt5Skin")}</div>
            <div className="settings-row-sub">{t("settings.mt5SkinSub")}</div>
          </div>
          <span className="settings-row-value">{skin === "mt5" ? t("settings.on") : t("settings.off")}</span>
        </button>

        {TRADING_ENABLED && (
          <button type="button" className="settings-row settings-row-btn" onClick={toggleMode}>
            <div className="settings-row-label">{t("settings.portfolioView")}</div>
            <span className="settings-row-value">{isAdvanced ? t("portfolio.advancedMode") : t("portfolio.simpleMode")}</span>
          </button>
        )}

        <button type="button" className="settings-row settings-row-btn" onClick={toggleExtraScans}>
          <div>
            <div className="settings-row-label">{t("settings.extraTechScans")}</div>
            <div className="settings-row-sub">{t("settings.extraTechScansSub")}</div>
          </div>
          <span className="settings-row-value">{extraScansEnabled ? t("settings.on") : t("settings.off")}</span>
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

        {TRADING_ENABLED && (
          <button
            type="button"
            className="settings-row settings-row-btn settings-row-danger"
            onClick={() => {
              if (confirm(t("settings.resetConfirm"))) reset();
            }}
          >
            <div className="settings-row-label">{t("settings.resetPortfolio")}</div>
          </button>
        )}

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
    </div>
  );
}
