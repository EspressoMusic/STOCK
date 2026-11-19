import { useCallback, useEffect, useState } from "react";
import { getPushKey, subscribePush, testPush, unsubscribePush } from "../api";

// The app's notifications on the phone (backend push.py): the strategies' alerts and the
// chart panel's price alerts. Needs https (the Tailscale address) and, on an iPhone, the app
// opened from the home screen. state: "unsupported" | "needs-https" | "needs-install" |
// "blocked" | "off" | "on" | "busy".
const IOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
const STANDALONE = window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;

function keyBytes(base64url) {
  const b64 = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

function initialState() {
  if (!window.isSecureContext) return "needs-https";
  if (IOS && !STANDALONE) return "needs-install";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  return "off";
}

export default function usePush() {
  const [state, setState] = useState(initialState);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (state !== "off") return;
    let stale = false;
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => {
        if (!stale && sub) {
          setState("on");
          subscribePush(sub.toJSON()).catch(() => {}); // the server may have lost it
        }
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
    // Only on the first look; toggle sets it afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = useCallback(async () => {
    if (state !== "on" && state !== "off") return;
    const was = state;
    setState("busy");
    setError(null);
    try {
      const reg = await navigator.serviceWorker.ready;
      if (was === "on") {
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await unsubscribePush(sub.toJSON()).catch(() => {});
          await sub.unsubscribe();
        }
        setState("off");
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setState(permission === "denied" ? "blocked" : "off");
        return;
      }
      const { public_key: key } = await getPushKey();
      if (!key) throw new Error("no key");
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      await subscribePush(sub.toJSON());
      setState("on");
      testPush().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
      setState(was);
    }
  }, [state]);

  return { state, error, toggle };
}
