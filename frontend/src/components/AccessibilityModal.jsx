import { useLang } from "../context/LangContext";

export default function AccessibilityModal({ a11y, onClose, onOpenStatement }) {
  const { t } = useLang();

  const fontScaleLabel = () => {
    if (a11y.fontScale === "large") return t("settings.fontSizeLarge");
    if (a11y.fontScale === "xlarge") return t("settings.fontSizeXLarge");
    return t("settings.fontSizeNormal");
  };

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal legal-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="name">{t("settings.accessibility")}</span>
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>

        <div className="legal-modal-body">
          <button type="button" className="settings-row settings-row-btn" onClick={a11y.cycleFontScale}>
            <div className="settings-row-label">{t("settings.fontSize")}</div>
            <span className="settings-row-value">{fontScaleLabel()}</span>
          </button>

          <button type="button" className="settings-row settings-row-btn" onClick={a11y.toggleHighContrast}>
            <div className="settings-row-label">{t("settings.highContrast")}</div>
            <span className="settings-row-value">{a11y.highContrast ? t("settings.on") : t("settings.off")}</span>
          </button>

          <button type="button" className="settings-row settings-row-btn" onClick={a11y.toggleReduceMotion}>
            <div>
              <div className="settings-row-label">{t("settings.reduceMotion")}</div>
              <div className="settings-row-sub">{t("settings.reduceMotionSub")}</div>
            </div>
            <span className="settings-row-value">{a11y.reduceMotion ? t("settings.on") : t("settings.off")}</span>
          </button>

          <button type="button" className="settings-row settings-row-btn" onClick={onOpenStatement}>
            <div className="settings-row-label">{t("settings.accessibilityStatement")}</div>
          </button>
        </div>
      </div>
    </div>
  );
}
