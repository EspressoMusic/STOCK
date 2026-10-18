import { useEffect, useRef, useState } from "react";
import { useLang } from "../context/LangContext";

// First-run sign-in choice: a real (server-verified) Google account, or an
// anonymous guest account — either way the backend issues a session so the
// choice is remembered. App.jsx owns the single useAuth() instance and only
// renders this gate while auth.user is empty, so once either path succeeds
// and sets the session, the parent re-render swaps this out on its own —
// no manual navigation needed here.
export default function WelcomeGate({ auth }) {
  const { t } = useLang();
  const { googleReady, googleConfigured, renderGoogleButton, continueAsGuest, loading } = auth;
  const buttonRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (googleReady && buttonRef.current) {
      renderGoogleButton(buttonRef.current, { theme: "outline", size: "large", shape: "pill", width: 280 });
    }
  }, [googleReady, renderGoogleButton]);

  const handleGuest = async () => {
    setError(null);
    try {
      await continueAsGuest();
    } catch {
      setError(t("welcome.error"));
    }
  };

  return (
    <div className="phone-shell">
      <div className="screen welcome-screen">
        <div className="welcome-hero">
          <h1 className="screen-title">{t("welcome.title")}</h1>
          <p className="screen-sub">{t("welcome.subtitle")}</p>
        </div>

        <div className="welcome-actions">
          {googleConfigured ? (
            <div ref={buttonRef} className="google-btn-slot" />
          ) : (
            <p className="screen-sub">{t("settings.googleNotConfigured")}</p>
          )}

          <div className="welcome-divider">
            <span>{t("welcome.or")}</span>
          </div>

          <button type="button" className="btn btn-ghost btn-block" onClick={handleGuest} disabled={loading}>
            {t("welcome.continueAsGuest")}
          </button>

          {error && <p className="screen-sub welcome-error">{error}</p>}
        </div>

        <p className="screen-sub welcome-fineprint">{t("welcome.fineprint")}</p>
      </div>
    </div>
  );
}
