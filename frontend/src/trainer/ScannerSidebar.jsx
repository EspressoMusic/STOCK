import { useEffect, useRef, useState } from "react";
import {
  getLatestScan,
  getLatestStochExtremeScan,
  getLatestTripleTouchScan,
  getLatestZoneTouchScan,
  runScan,
  runStochExtremeScan,
  runTripleTouchScan,
  runZoneTouchScan,
} from "../api";
import { formatDateTime, formatPercent, formatPrice } from "../format";
import { getHotSignal } from "../lib/hotSignal";
import "./scanner.css";

// The app's stock scans (the "interesting" screen) as a side list next to the chart:
// clicking a row — or ↑/↓ — opens that symbol on the chart. It stays mounted while
// closed, so the scans and ↑/↓ keep working with the chart at full width.

function formatStockPrice(value) {
  if (value < 1) return `$${value.toPrecision(4)}`;
  return formatPrice(value);
}

// The movers scans store naive UTC datetimes, so they serialize without a zone suffix.
function asUtc(iso) {
  if (!iso || /(Z|[+-]\d{2}:?\d{2})$/.test(iso)) return iso;
  return `${iso}Z`;
}

const CATEGORY_KEY = "trainer-scanner-category";

function readCategory() {
  try {
    return localStorage.getItem(CATEGORY_KEY);
  } catch {
    return null;
  }
}

function errorText(err) {
  return err instanceof TypeError ? "השרת לא זמין — צריך שה-backend ירוץ (פורט 8001)" : err.message;
}

function moversCategory(id, label, badgeField, tone) {
  return {
    id,
    label,
    latest: () => getLatestScan({ direction: id }),
    run: () => runScan({ direction: id }),
    toView: (data) => ({
      scannedAt: asUtc(data.created_at),
      rows: data.results.map((s) => ({
        symbol: s.symbol,
        badge: formatPercent(s[badgeField]),
        tone,
        price: formatPrice(s.price),
        hot: getHotSignal(s).isHot,
      })),
    }),
  };
}

// autoRun: like the app, the slow technical scans start on their own when nothing is
// cached yet; the movers scans wait for "סרוק".
const SCAN_CATEGORIES = [
  moversCategory("losers", "נפלו קיצוני", "change_percent", "down"),
  moversCategory("gainers", "עלו קיצוני", "change_percent", "up"),
  moversCategory("broken", "מניות שבורות", "fifty_two_week_change_percent", "broken"),
  {
    id: "triple-touch",
    label: "שילה",
    autoRun: true,
    latest: () => getLatestTripleTouchScan("stocks"),
    run: () => runTripleTouchScan("stocks"),
    toView: (data) => ({
      scannedAt: data.scanned_at,
      rows: data.results.map((c) => ({
        symbol: c.symbol,
        badge: `${c.direction === "long" ? "לונג" : "שורט"} ×${c.touch_count}`,
        tone: c.direction === "long" ? "up" : "down",
        price: formatStockPrice(c.price),
        hot: c.at_level,
      })),
    }),
  },
  {
    id: "stoch-extreme",
    label: "שילה 2",
    autoRun: true,
    latest: getLatestStochExtremeScan,
    run: runStochExtremeScan,
    toView: (data) => ({
      scannedAt: data.scanned_at,
      rows: data.stocks.map((c) => ({
        symbol: c.symbol,
        badge: c.signal === "overbought" ? "קנייתי-יתר" : "מכירתי-יתר",
        tone: c.signal,
        price: formatStockPrice(c.price),
        hot: false,
      })),
    }),
  },
  {
    // 4h chart: a zone price turned at 3+ times, and the last candle closed inside it.
    id: "zone-touch",
    label: "3 נגיעות",
    autoRun: true,
    latest: getLatestZoneTouchScan,
    run: runZoneTouchScan,
    toView: (data) => ({
      scannedAt: data.scanned_at,
      rows: data.results.map((c) => ({
        symbol: c.symbol,
        badge: `באזור ×${c.touch_count}`,
        tone: "broken",
        price: formatStockPrice(c.price),
        hot: false,
      })),
    }),
  },
];

export default function ScannerSidebar({ open, symbol, onPick }) {
  const [category, setCategory] = useState(readCategory);
  // Per category: { view, loading, scanning, error } — kept when switching, so a slow
  // scan that finishes in the background is still there on the way back.
  const [scans, setScans] = useState({});
  const startedRef = useRef(new Set());
  const listRef = useRef(null);

  const current = SCAN_CATEGORIES.find((c) => c.id === category) ?? SCAN_CATEGORIES[0];
  const state = scans[current.id] ?? { loading: true };
  const rows = state.view?.rows ?? [];
  const activeIndex = rows.findIndex((r) => r.symbol === symbol);

  const patch = (id, next) => setScans((prev) => ({ ...prev, [id]: { ...prev[id], ...next } }));

  const scan = (cat) => {
    patch(cat.id, { scanning: true, error: null });
    cat
      .run()
      .then((data) => patch(cat.id, { view: cat.toView(data), scanning: false }))
      .catch((err) => patch(cat.id, { error: errorText(err), scanning: false }));
  };

  useEffect(() => {
    try {
      localStorage.setItem(CATEGORY_KEY, current.id);
    } catch {
      /* private mode etc. — the choice just doesn't stick */
    }
  }, [current.id]);

  // First visit to a category: show its last cached scan. Nothing loads (or starts a
  // slow scan) until the scanner is opened.
  useEffect(() => {
    if (!open || startedRef.current.has(current.id)) return;
    startedRef.current.add(current.id);
    patch(current.id, { loading: true });
    current
      .latest()
      .then((data) => patch(current.id, { view: current.toView(data), loading: false }))
      .catch((err) => {
        if (current.autoRun && !(err instanceof TypeError)) {
          patch(current.id, { loading: false });
          scan(current);
        } else {
          patch(current.id, { loading: false, error: errorText(err) });
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.id, open]);

  // ↑/↓ step through the list (unless typing in the search box).
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      if (!rows.length || e.target.closest?.("input, textarea, select")) return;
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      const next =
        activeIndex < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, activeIndex + step));
      if (next !== activeIndex) onPick(rows[next].symbol);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, activeIndex, onPick]);

  useEffect(() => {
    listRef.current?.querySelector(".stock-row.active")?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, current.id, open]);

  return (
    <aside className="trainer-scanner" hidden={!open}>
      <div className="trainer-scanner-top">
        <b>סורק מניות</b>
        <button type="button" className="trainer-scan-btn" onClick={() => scan(current)} disabled={state.scanning}>
          {state.scanning ? "סורק..." : "סרוק"}
        </button>
      </div>

      <div className="trainer-scanner-cats">
        {SCAN_CATEGORIES.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`chip ${c.id === current.id ? "active" : ""}`}
            onClick={() => setCategory(c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>

      {state.view?.scannedAt && (
        <p className="trainer-scanner-note">
          סריקה אחרונה: {formatDateTime(state.view.scannedAt)} · {rows.length} מניות
        </p>
      )}

      <div className="trainer-scanner-list" ref={listRef}>
        {state.loading && <p className="trainer-scanner-msg">טוען...</p>}
        {!state.loading && state.scanning && !state.view && (
          <p className="trainer-scanner-msg">סורק... זה יכול לקחת דקה-שתיים בסריקה ראשונה.</p>
        )}
        {state.error && <p className="trainer-scanner-msg trainer-error">{state.error}</p>}
        {!state.loading && !state.error && state.view && rows.length === 0 && (
          <p className="trainer-scanner-msg">אין תוצאות כרגע.</p>
        )}
        {rows.map((r) => (
          <button
            key={r.symbol}
            type="button"
            className={`stock-row${r.hot ? " stock-row-hot" : ""}${r.symbol === symbol ? " active" : ""}`}
            onClick={() => onPick(r.symbol)}
          >
            <span className="stock-row-symbol">{r.symbol}</span>
            <span className={`chg-badge chg-${r.tone}`}>{r.badge}</span>
            <span className="stock-row-price">{r.price}</span>
          </button>
        ))}
      </div>

      {rows.length > 0 && <p className="trainer-scanner-note">↑ ↓ במקלדת — מניה קודמת / הבאה</p>}
    </aside>
  );
}
