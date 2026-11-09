import { useCallback, useEffect, useState } from "react";
import { useLang } from "../context/LangContext";
import { getSignals, signalPictureUrl } from "../api";

// The weekly stocks, stock day trading and forex bots' alerts, newest first, each with the
// chart picture it went out with — a tap opens it full size. This week's on top; once a week
// ends, its signals move to the history below, a week to a row, opened with a tap (the user,
// 9/11/26).

const KINDS = ["weekly", "day", "forex"];
const REFRESH_INTERVAL_MS = 60000;

function dayLabel(date, lang) {
  return date.toLocaleDateString(lang === "en" ? "en-GB" : "he-IL", { weekday: "long", day: "2-digit", month: "2-digit" });
}

function shortDate(date, lang) {
  return date.toLocaleDateString(lang === "en" ? "en-GB" : "he-IL", { day: "2-digit", month: "2-digit" });
}

// The Sunday the date's week starts on, at midnight local time.
function weekStart(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - d.getDay());
  return d;
}

// [week start, its signals], in the list's order.
function byWeek(signals) {
  const groups = [];
  for (const s of signals) {
    const start = weekStart(new Date(s.created_at));
    if (groups.at(-1)?.[0].getTime() !== start.getTime()) groups.push([start, []]);
    groups.at(-1)[1].push(s);
  }
  return groups;
}

function timeLabel(date, lang) {
  return date.toLocaleTimeString(lang === "en" ? "en-GB" : "he-IL", { hour: "2-digit", minute: "2-digit" });
}

// [day label, its signals], in the list's order.
function byDay(signals, lang) {
  const groups = [];
  for (const s of signals) {
    const label = dayLabel(new Date(s.created_at), lang);
    if (groups.at(-1)?.[0] !== label) groups.push([label, []]);
    groups.at(-1)[1].push(s);
  }
  return groups;
}

function SignalCard({ signal, onOpen }) {
  const { t, lang } = useLang();
  const side = signal.direction === "up" ? "bullish" : signal.direction === "down" ? "bearish" : null;
  return (
    <article className="signal-card">
      <div className="signal-card-head">
        {side && <span className={`badge-pill ${side}`}>{t(side === "bullish" ? "signals.long" : "signals.short")}</span>}
        <a className="signal-card-symbol" href={signal.tradingview} target="_blank" rel="noreferrer">
          {signal.name || signal.symbol}
        </a>
        {signal.strong && <span className="badge-pill neutral">{t("signals.strong")}</span>}
        {signal.example && <span className="badge-pill neutral">{t("signals.example")}</span>}
        <span className="signal-card-time">{timeLabel(new Date(signal.created_at), lang)}</span>
      </div>
      {signal.has_picture ? (
        <button type="button" className="signal-card-picture" onClick={() => onOpen(signal)}>
          <img src={signalPictureUrl(signal.id)} alt={signal.symbol} loading="lazy" />
        </button>
      ) : (
        <p className="signal-card-missing">{t("signals.noPicture")}</p>
      )}
      {signal.details && <p className="signal-card-details">{signal.details}</p>}
    </article>
  );
}

function Days({ signals, onOpen }) {
  const { lang } = useLang();
  return byDay(signals, lang).map(([day, list]) => (
    <section key={day} className="signals-day">
      <h3 className="signals-day-title">{day}</h3>
      {list.map((s) => (
        <SignalCard key={s.id} signal={s} onOpen={onOpen} />
      ))}
    </section>
  ));
}

// A past week, closed until tapped, so its pictures load only then.
function PastWeek({ start, signals, onOpen }) {
  const { t, lang } = useLang();
  const [open, setOpen] = useState(false);
  const end = new Date(start);
  end.setDate(end.getDate() + 6); // by the calendar: a clock change makes one day 23 or 25 hours
  return (
    <section className="signals-week">
      <button type="button" className={`signals-week-head ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}>
        <span>
          {t("signals.week")} {shortDate(start, lang)} – {shortDate(end, lang)}
        </span>
        <span className="signals-week-count">
          {signals.length} {t("signals.count")} {open ? "▴" : "▾"}
        </span>
      </button>
      {open && <Days signals={signals} onOpen={onOpen} />}
    </section>
  );
}

function PictureViewer({ signal, onClose }) {
  const { t } = useLang();
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="signal-viewer" onClick={onClose}>
      <button type="button" className="chart-modal-close signal-viewer-close" onClick={onClose} aria-label={t("signals.close")}>
        ✕
      </button>
      <img src={signalPictureUrl(signal.id)} alt={signal.symbol} onClick={(e) => e.stopPropagation()} />
    </div>
  );
}

export default function SignalsScreen() {
  const { t } = useLang();
  const [kind, setKind] = useState(KINDS[0]);
  // kind -> its signals, kept so switching back shows them at once.
  const [lists, setLists] = useState({});
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);

  const load = useCallback((which) => {
    getSignals(which)
      .then((list) => {
        setLists((prev) => ({ ...prev, [which]: list }));
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    load(kind);
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load(kind);
    }, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [kind, load]);

  const list = lists[kind];
  const closeViewer = useCallback(() => setOpen(null), []);
  const thisWeek = weekStart(new Date()).getTime();
  const weeks = list ? byWeek(list) : [];
  const current = weeks.find(([start]) => start.getTime() === thisWeek)?.[1] ?? [];
  const past = weeks.filter(([start]) => start.getTime() < thisWeek);

  return (
    <div className="screen signals-screen">
      <h2 className="watchlist-box-title">{t("signals.title")}</h2>
      <div className="chip-row signals-tabs">
        {KINDS.map((k) => (
          <button key={k} type="button" className={`chip ${kind === k ? "active" : ""}`} onClick={() => setKind(k)}>
            {t(`signals.${k}`)}
          </button>
        ))}
      </div>

      {error && !list && (
        <div className="state-msg">
          <p>{t("signals.error")}</p>
          <button type="button" onClick={() => load(kind)}>
            {t("signals.retry")}
          </button>
        </div>
      )}
      {!error && !list && <p className="chart-loading">{t("signals.loading")}</p>}
      {list && list.length === 0 && <p className="watchlist-box-empty">{t("signals.empty")}</p>}
      {list && list.length > 0 && (
        <>
          <p className="signals-count">
            {list.length} {t("signals.count")}
          </p>
          <h3 className="signals-section-title">{t("signals.thisWeek")}</h3>
          {current.length > 0 ? (
            <Days signals={current} onOpen={setOpen} />
          ) : (
            <p className="watchlist-box-empty">{t("signals.thisWeekEmpty")}</p>
          )}
          {past.length > 0 && (
            <>
              <h3 className="signals-section-title">{t("signals.history")}</h3>
              {past.map(([start, signals]) => (
                <PastWeek key={start.getTime()} start={start} signals={signals} onOpen={setOpen} />
              ))}
            </>
          )}
        </>
      )}

      {open && <PictureViewer signal={open} onClose={closeViewer} />}
    </div>
  );
}
