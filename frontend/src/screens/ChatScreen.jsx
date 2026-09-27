import { useEffect, useRef, useState } from "react";
import useChatHistory from "../hooks/useChatHistory";
import usePortfolio from "../hooks/usePortfolio";
import { sendChatMessage } from "../api";
import { useLang } from "../context/LangContext";
import { formatPrice, formatPercent } from "../format";

export default function ChatScreen() {
  const { t } = useLang();
  const { messages, addMessage } = useChatHistory();
  const { cash, holdings, buy } = usePortfolio();
  const [text, setText] = useState("");
  const [imageFile, setImageFile] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [sending, setSending] = useState(false);
  const listRef = useRef(null);
  const imageInputRef = useRef(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, sending]);

  const handlePickImage = (e) => {
    const picked = e.target.files?.[0];
    if (!picked) return;
    setImageFile(picked);
    setImagePreview(URL.createObjectURL(picked));
  };

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed && !imageFile) return;

    const userMessage = {
      role: "user",
      content: trimmed || t("chat.defaultImageQuestion"),
      imagePreview: imagePreview || null,
    };
    const historyForRequest = [...messages, userMessage].map((m) => ({ role: m.role, content: m.content }));
    addMessage(userMessage);

    const fileToSend = imageFile;
    setText("");
    setImageFile(null);
    setImagePreview(null);
    setSending(true);

    sendChatMessage(historyForRequest, fileToSend)
      .then((res) => addMessage({ role: "assistant", content: res.reply, suggestions: res.suggestions || [] }))
      .catch((err) => addMessage({ role: "assistant", content: `${t("chat.error")} ${err.message}` }))
      .finally(() => setSending(false));
  };

  const handleSaveStock = (stock) => {
    const cost = stock.price || 0;
    if (cost <= 0 || cost > cash) return;
    buy(stock.symbol, stock.name, 1, stock.price);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="screen chat-screen">
      <div className="chat-messages" ref={listRef}>
        {messages.length === 0 && !sending && (
          <div className="chat-bubble-row bot">
            <div className="chat-bubble">{t("chat.greeting")}</div>
          </div>
        )}
        {messages.map((m, i) => (
          <div className={`chat-bubble-row ${m.role === "user" ? "user" : "bot"}`} key={i}>
            <div className="chat-bubble">
              {m.content}
              {m.imagePreview && <img src={m.imagePreview} alt={t("chat.imageAttached")} />}
              {m.suggestions?.length > 0 && (
                <div className="chat-stock-suggestions">
                  {m.suggestions.map((stock) => {
                    const owned = holdings.some((h) => h.symbol === stock.symbol);
                    const isUp = (stock.change_percent ?? 0) >= 0;
                    return (
                      <button
                        key={stock.symbol}
                        type="button"
                        className={`chat-stock-card${owned ? " saved" : ""}`}
                        onClick={() => handleSaveStock(stock)}
                        disabled={owned}
                      >
                        <span className="chat-stock-symbol">{stock.symbol}</span>
                        <span className="chat-stock-price">{formatPrice(stock.price)}</span>
                        {stock.change_percent != null && (
                          <span className={`chg-badge ${isUp ? "chg-up" : "chg-down"}`}>
                            {formatPercent(stock.change_percent)}
                          </span>
                        )}
                        <span className="chat-stock-save">{owned ? t("chat.savedStock") : t("chat.saveStock")}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="chat-bubble-row bot">
            <div className="chat-bubble chat-typing" role="status" aria-label={t("chat.thinking")}>
              <span className="chat-typing-dot" />
              <span className="chat-typing-dot" />
              <span className="chat-typing-dot" />
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-bar">
        {imagePreview && (
          <div className="chat-attach-preview">
            <img src={imagePreview} alt={t("chat.imageAttached")} />
            <span>{t("chat.imageAttached")}</span>
            <button
              onClick={() => {
                setImageFile(null);
                setImagePreview(null);
              }}
            >
              ✕
            </button>
          </div>
        )}
        <div className="chat-input-row">
          <button className="chat-icon-btn send" onClick={handleSend} disabled={sending}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
          <input
            type="text"
            placeholder={t("chat.placeholder")}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button className="chat-icon-btn" onClick={() => imageInputRef.current?.click()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 8a1 1 0 0 1 1-1h2.2l1-1.6A1 1 0 0 1 9.05 5h5.9a1 1 0 0 1 .85.4L16.8 7H19a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8Z" />
              <circle cx="12" cy="13" r="3.3" />
            </svg>
          </button>
          <input ref={imageInputRef} type="file" accept="image/*" hidden onChange={handlePickImage} />
        </div>
      </div>
    </div>
  );
}
