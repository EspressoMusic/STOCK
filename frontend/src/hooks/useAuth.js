import { useCallback, useEffect, useState } from "react";
import { authAsGuest, authLogout, authWithGoogle, getCurrentUser } from "../api";

const STORAGE_KEY = "auth-session-v1";
const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

function readSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeSession(session) {
  if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  else localStorage.removeItem(STORAGE_KEY);
}

// Real sign-in: Google accounts are verified server-side (see app/auth.py),
// guests get an anonymous account too, so both end up with the same kind of
// backend session token. Google Identity Services itself is loaded here the
// same way the old client-only useGoogleAuth did; the difference is the raw
// credential now goes to the backend instead of being decoded in the browser.
export default function useAuth() {
  const [session, setSession] = useState(readSession);
  const [gsiReady, setGsiReady] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!CLIENT_ID) return;
    if (window.google?.accounts?.id) {
      setGsiReady(true);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => setGsiReady(true);
    document.head.appendChild(script);
  }, []);

  // Confirm a stored session is still valid (the backend db can be wiped in
  // dev, or a guest could theoretically be pruned later) — drop it silently
  // if not, so the app falls back to the welcome gate instead of a broken state.
  useEffect(() => {
    if (!session?.token) return;
    getCurrentUser(session.token).catch(() => {
      setSession(null);
      writeSession(null);
    });
  }, [session?.token]);

  const finishLogin = useCallback((result) => {
    const next = { token: result.token, user: result.user };
    setSession(next);
    writeSession(next);
    return next.user;
  }, []);

  const renderGoogleButton = useCallback(
    (el, options) => {
      if (!gsiReady || !CLIENT_ID || !el) return;
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: async (response) => {
          setLoading(true);
          try {
            const result = await authWithGoogle(response.credential);
            finishLogin(result);
          } catch {
            /* the button stays put so the user can retry */
          } finally {
            setLoading(false);
          }
        },
      });
      window.google.accounts.id.renderButton(el, options);
    },
    [gsiReady, finishLogin]
  );

  const continueAsGuest = useCallback(async () => {
    setLoading(true);
    try {
      const result = await authAsGuest();
      return finishLogin(result);
    } finally {
      setLoading(false);
    }
  }, [finishLogin]);

  const signOut = useCallback(() => {
    if (session?.token) authLogout(session.token).catch(() => {});
    setSession(null);
    writeSession(null);
    window.google?.accounts?.id?.disableAutoSelect?.();
  }, [session]);

  return {
    user: session?.user ?? null,
    loading,
    googleReady: gsiReady && Boolean(CLIENT_ID),
    googleConfigured: Boolean(CLIENT_ID),
    renderGoogleButton,
    continueAsGuest,
    signOut,
  };
}
