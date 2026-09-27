import { useLang } from "../context/LangContext";

export default function LegalModal({ doc, onClose }) {
  const { t } = useLang();
  const content = t(`legal.${doc}`);
  const contactEmail = t("legal.contactEmail");

  if (!content || typeof content === "string") return null;

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal legal-modal" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="name">{content.title}</span>
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>

        <div className="legal-modal-body">
          <p className="legal-updated">{content.updated}</p>

          {content.sections.map((section) => (
            <div className="legal-section" key={section.heading}>
              <h3>{section.heading}</h3>
              {section.body.map((paragraph, i) => (
                <p key={i}>{paragraph}</p>
              ))}
            </div>
          ))}

          <p className="legal-contact">{contactEmail}</p>
        </div>
      </div>
    </div>
  );
}
