import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "demo-google-user-v1";
const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

function decodeJwt(token) {
  try {
    const payload = token.split(".")[1];
    const json = decodeURIComponent(
      atob(payload.replace(/-/g, "+").replace(/_/g, "/"))
        .split("")
        .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
        .join("")
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

// Client-only Google Identity Services integration — no backend session exists in this
// demo app, so the JWT is decoded locally just to display name/email/picture. Requires
// VITE_GOOGLE_CLIENT_ID (see frontend/.env.example) from the user's own Google Cloud project.
export default function useGoogleAuth() {
  const [user, setUser] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!CLIENT_ID) return;
    if (window.google?.accounts?.id) {
      setReady(true);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => setReady(true);
    document.head.appendChild(script);
  }, []);

  useEffect(() => {
    if (!ready || !CLIENT_ID) return;
    window.google.accounts.id.initialize({
      client_id: CLIENT_ID,
      callback: (response) => {
        const payload = decodeJwt(response.credential);
        if (!payload) return;
        const nextUser = { name: payload.name, email: payload.email, picture: payload.picture };
        setUser(nextUser);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(nextUser));
      },
    });
  }, [ready]);

  const renderButton = useCallback(
    (el, options) => {
      if (!ready || !CLIENT_ID || !el) return;
      window.google.accounts.id.renderButton(el, options);
    },
    [ready]
  );

  const signOut = useCallback(() => {
    setUser(null);
    localStorage.removeItem(STORAGE_KEY);
    window.google?.accounts?.id?.disableAutoSelect?.();
  }, []);

  return { user, ready: ready && Boolean(CLIENT_ID), configured: Boolean(CLIENT_ID), renderButton, signOut };
}
