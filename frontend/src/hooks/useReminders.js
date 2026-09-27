import { useCallback, useEffect, useState } from "react";

const ENABLED_KEY = "demo-reminders-enabled-v1";
const LAST_KEY = "demo-reminders-last-v1";
const INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h — while the tab stays open, no push server involved.

export default function useReminders(t) {
  const supported = typeof window !== "undefined" && "Notification" in window;
  const [enabled, setEnabled] = useState(() => supported && localStorage.getItem(ENABLED_KEY) === "1");
  const [permission, setPermission] = useState(() => (supported ? Notification.permission : "denied"));

  useEffect(() => {
    if (!supported) return;
    localStorage.setItem(ENABLED_KEY, enabled ? "1" : "0");
  }, [enabled, supported]);

  useEffect(() => {
    if (!supported || !enabled || permission !== "granted") return;
    const check = () => {
      const last = Number(localStorage.getItem(LAST_KEY) || 0);
      if (Date.now() - last >= INTERVAL_MS) {
        new Notification(t("reminders.title"), { body: t("reminders.body") });
        localStorage.setItem(LAST_KEY, String(Date.now()));
      }
    };
    check();
    const id = setInterval(check, 60_000);
    return () => clearInterval(id);
  }, [enabled, permission, supported, t]);

  const toggle = useCallback(async () => {
    if (!supported) return;
    if (enabled) {
      setEnabled(false);
      return;
    }
    let perm = Notification.permission;
    if (perm === "default") perm = await Notification.requestPermission();
    setPermission(perm);
    if (perm !== "granted") return;
    new Notification(t("reminders.enabledTitle"), { body: t("reminders.enabledBody") });
    localStorage.setItem(LAST_KEY, String(Date.now()));
    setEnabled(true);
  }, [enabled, supported, t]);

  return { enabled, toggle, supported, permission };
}
