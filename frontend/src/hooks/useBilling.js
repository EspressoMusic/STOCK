import { useCallback, useEffect, useState } from "react";
import { getBillingStatus } from "../api";

let paddleScriptPromise = null;

function loadPaddleScript() {
  if (window.Paddle) return Promise.resolve();
  if (!paddleScriptPromise) {
    paddleScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.paddle.com/paddle/v2/paddle.js";
      script.async = true;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }
  return paddleScriptPromise;
}

// Free plan never touches OpenAI (enforced server-side in app/license.py) —
// this hook just reflects that plan state and, when the app owner has a
// Paddle account configured, opens the Paddle Checkout overlay to upgrade.
export default function useBilling() {
  const [status, setStatus] = useState(null);
  const [paddleReady, setPaddleReady] = useState(false);

  const refresh = useCallback(() => {
    getBillingStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!status?.paddle_client_token || !status?.paddle_price_id) return;
    let cancelled = false;
    loadPaddleScript()
      .then(() => {
        if (cancelled) return;
        window.Paddle.Environment.set(status.paddle_environment === "production" ? "production" : "sandbox");
        window.Paddle.Initialize({ token: status.paddle_client_token });
        setPaddleReady(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [status]);

  const openCheckout = useCallback(() => {
    if (!paddleReady || !status?.paddle_price_id) return;
    window.Paddle.Checkout.open({
      items: [{ priceId: status.paddle_price_id, quantity: 1 }],
      eventCallback: (event) => {
        if (event?.name === "checkout.completed") {
          // The webhook is the source of truth and may take a moment to
          // arrive; give it a beat before re-reading plan status.
          setTimeout(refresh, 3000);
        }
      },
    });
  }, [paddleReady, status, refresh]);

  return {
    plan: status?.plan ?? "free",
    aiEnabled: status?.ai_enabled ?? false,
    canCheckout: paddleReady && Boolean(status?.paddle_price_id),
    openCheckout,
    refresh,
  };
}
