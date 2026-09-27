import { useLang } from "../context/LangContext";
import ChatScreen from "../screens/ChatScreen";

export default function ChatModal({ onClose }) {
  const { t } = useLang();

  return (
    <div className="chart-modal-overlay" onClick={onClose}>
      <div className="chart-modal chat-modal-shell" onClick={(e) => e.stopPropagation()}>
        <div className="chart-modal-head">
          <div className="chart-modal-title">
            <span className="name">{t("nav.chat")}</span>
          </div>
          <button className="chart-modal-close" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>

        <div className="chat-modal-body">
          <ChatScreen />
        </div>
      </div>
    </div>
  );
}
