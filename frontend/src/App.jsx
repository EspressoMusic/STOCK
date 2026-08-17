import { useEffect, useMemo, useState, useCallback } from "react";
import FilterBar from "./components/FilterBar";
import StockCard from "./components/StockCard";
import ThemeToggle from "./components/ThemeToggle";
import { getStatus, getSectors, getLatestScan, runScan } from "./api";
import { formatDateTime } from "./format";

function getInitialTheme() {
  const stored = localStorage.getItem("theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function App() {
  const [mode, setMode] = useState("movers"); // "movers" | "broken"
  const [direction, setDirection] = useState("losers"); // only relevant when mode === "movers"
  const [sector, setSector] = useState("");
  const [maxPrice, setMaxPrice] = useState("");

  const [sectors, setSectors] = useState([]);
  const [status, setStatus] = useState(null);
  const [scan, setScan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(getInitialTheme);

  const effectiveDirection = mode === "broken" ? "broken" : direction;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    getSectors().then(setSectors).catch(() => {});
    getStatus().then(setStatus).catch(() => {});
  }, []);

  const loadLatest = useCallback(() => {
    setLoading(true);
    setError(null);
    getLatestScan({ direction: effectiveDirection, category: sector || undefined })
      .then((data) => setScan(data))
      .catch((err) => {
        setScan(null);
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, [effectiveDirection, sector]);

  useEffect(() => {
    loadLatest();
  }, [loadLatest]);

  const handleRescan = () => {
    setScanning(true);
    setError(null);
    runScan({
      direction: effectiveDirection,
      sector: sector || null,
      maxPrice: maxPrice ? Number(maxPrice) : null,
    })
      .then((data) => {
        setScan(data);
        return getStatus().then(setStatus);
      })
      .catch((err) => setError(err.message))
      .finally(() => setScanning(false));
  };

  const filteredResults = useMemo(() => {
    if (!scan) return [];
    const cap = maxPrice ? Number(maxPrice) : null;
    return scan.results.filter((r) => !cap || (r.price ?? 0) <= cap);
  }, [scan, maxPrice]);

  const nextScanLabel = useMemo(() => {
    if (!status?.next_scans) return null;
    const keys = mode === "broken" ? ["scan_broken"] : ["scan_morning", "scan_afternoon"];
    const times = keys.map((k) => status.next_scans[k]).filter(Boolean).map((t) => new Date(t));
    if (times.length === 0) return null;
    const next = new Date(Math.min(...times));
    return formatDateTime(next.toISOString());
  }, [status, mode]);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>סורק הקיצוניות</h1>
          <p className="subtitle">מניות נאסד"ק שהתרסקו או זינקו קיצונית</p>
        </div>
        <div className="header-side">
          <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === "dark" ? "light" : "dark"))} />
          <div className="status-box">
            {scan && <div>סריקה אחרונה: {formatDateTime(scan.created_at)}</div>}
            {scan?.total_matches != null && (
              <div>
                מוצגות {filteredResults.length} מתוך {scan.total_matches} שנמצאו
              </div>
            )}
            {nextScanLabel && <div>סריקה הבאה: {nextScanLabel}</div>}
            {status && !status.openai_configured && (
              <div className="warn">⚠ אין מפתח OpenAI מוגדר — מוצג סיכום גנרי בלבד</div>
            )}
          </div>
        </div>
      </header>

      <nav className="mode-tabs">
        <button className={mode === "movers" ? "active" : ""} onClick={() => setMode("movers")}>
          סריקה יומית
        </button>
        <button className={mode === "broken" ? "active" : ""} onClick={() => setMode("broken")}>
          מניות שבורות
        </button>
      </nav>
      {mode === "broken" && (
        <p className="mode-desc">
          מניות פני-סטוק שכבר זמן רב שוות כמעט כלום — לא קשור לתנועה של היום, אלא לרשימת מעקב קבועה
          שמתעדכנת פעם ביום.
        </p>
      )}

      <FilterBar
        showDirectionToggle={mode === "movers"}
        direction={direction}
        onDirectionChange={setDirection}
        sector={sector}
        onSectorChange={setSector}
        sectors={sectors}
        maxPrice={maxPrice}
        onMaxPriceChange={setMaxPrice}
        onRescan={handleRescan}
        scanning={scanning}
      />

      <main>
        {loading && <p className="state-msg">טוען...</p>}
        {!loading && error && (
          <div className="state-msg">
            <p>{error}</p>
            <button onClick={handleRescan} disabled={scanning}>
              {scanning ? "סורק..." : "הרץ סריקה ראשונה"}
            </button>
          </div>
        )}
        {!loading && !error && filteredResults.length === 0 && (
          <p className="state-msg">אין מניות שעונות על הסינון הנוכחי.</p>
        )}
        {!loading && !error && filteredResults.length > 0 && (
          <div className="stock-grid">
            {filteredResults.map((stock) => (
              <StockCard key={stock.id} stock={stock} direction={effectiveDirection} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
