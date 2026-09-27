import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "demo-chat-history-v1";

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// Keeps the chat transcript in localStorage so the bot "remembers" across
// reloads on this device — the backend itself is stateless per-request.
export default function useChatHistory() {
  const [messages, setMessages] = useState(load);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  }, [messages]);

  const addMessage = useCallback((message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  const clear = useCallback(() => setMessages([]), []);

  return { messages, addMessage, clear };
}
