import { useCallback, useEffect, useState } from "react";
import { useLang } from "../context/LangContext";
import { getModelChallenge } from "../api";

// The models' test (the user, 10/11/26): each new model picks an asset, a timeframe and a
// strategy, and this page shows whether it makes money month by month. The picks and their
// rules live in the backend's model_challenge.py; the months are worked out again on every
// load, so the ones after the pick are a real forward test.

function signed(pct, digits = 1) {
  return `${pct > 0 ? "+" : ""}${pct.toFixed(digits)}%`;
}

function tone(pct) {
  return pct > 0 ? "is-up" : pct < 0 ? "is-down" : "";
}

// [year, its 12 months (null where there's none)], oldest first.
function byYear(months) {
  const years = new Map();
  for (const m of months) {
    const [year, month] = m.month.split("-");
    if (!years.has(year)) years.set(year, Array(12).fill(null));
    years.get(year)[Number(month) - 1] = m;
  }
  return [...years];
}

function Stat({ label, value, className = "" }) {
  return (
    <div className="model-stat">
      <span className={`model-stat-value ${className}`}>{value}</span>
      <span className="model-stat-label">{label}</span>
    </div>
  );
}

function ModelCard({ entry }) {
  const { t, lang } = useLang();
  const { test, hold } = entry;
  const picked = new Date(entry.picked_at).toLocaleDateString(lang === "en" ? "en-GB" : "he-IL");
  return (
    <article className="signal-card model-card">
      <div className="signal-card-head">
        <span className={`badge-pill ${entry.passed ? "bullish" : "bearish"}`}>{t(entry.passed ? "models.passed" : "models.failed")}</span>
        <span className="signal-card-symbol">{entry.model}</span>
        <span className="signal-card-time">{picked}</span>
      </div>

      <dl className="model-pick">
        <dt>{t("models.asset")}</dt>
        <dd>{entry.asset}</dd>
        <dt>{t("models.timeframe")}</dt>
        <dd>{entry.timeframe}</dd>
        <dt>{t("models.strategy")}</dt>
        <dd>{entry.strategy}</dd>
      </dl>

      <div className="model-stats">
        <Stat label={t("models.avgMonth")} value={signed(test.avg, 2)} className={tone(test.avg)} />
        <Stat label={t("models.positiveMonths")} value={`${test.positive}/${test.months}`} />
        <Stat label={t("models.worstMonth")} value={signed(test.worst)} className="is-down" />
        <Stat label={t("models.drawdown")} value={signed(test.drawdown)} className="is-down" />
      </div>

      <p className="model-line">
        {t("models.last12")}: <b className={tone(entry.last12_avg)}>{signed(entry.last12_avg, 2)}</b> {t("models.perMonth")}
      </p>
      <p className="model-line">
        {t("models.trades")}: <b>{entry.trades.count}</b>, {t("models.wins")}{" "}
        <b>{Math.round((entry.trades.wins / entry.trades.count) * 100)}%</b>
      </p>
      <p className="model-line">
        {t("models.hold")}: <b>{signed(hold.avg, 2)}</b> {t("models.perMonth")}, {t("models.drawdown")} <b>{signed(hold.drawdown)}</b>
      </p>
      <p className="model-line">
        {t("models.sincePick")}:{" "}
        {entry.since_pick.days > 0 ? (
          <b className={tone(entry.since_pick.total)}>{signed(entry.since_pick.total, 2)}</b>
        ) : (
          t("models.sincePickNone")
        )}
      </p>
      <p className="model-line">
        {t("models.nextOrders")}: <b>±${entry.next_distance.toFixed(2)}</b>
      </p>

      <div className="model-months" dir="ltr">
        {byYear(entry.months).map(([year, months]) => (
          <div key={year} className="model-year">
            <span className="model-year-label">{year}</span>
            {months.map((m, i) => (
              <span
                key={i}
                className={`model-month ${m ? tone(m.pct) : "is-empty"} ${m?.live ? "is-live" : ""}`}
                title={m ? `${m.month}: ${signed(m.pct, 2)}` : ""}
              >
                {m ? Math.abs(m.pct).toFixed(Math.abs(m.pct) >= 10 ? 0 : 1) : ""}
              </span>
            ))}
          </div>
        ))}
      </div>
      <p className="model-legend">{t("models.legend")}</p>

      <p className="signal-card-details">{entry.note}</p>
      <ol className="model-rules">
        {entry.rules.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ol>
    </article>
  );
}

export default function ModelsScreen() {
  const { t } = useLang();
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    getModelChallenge()
      .then(setEntries)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(load, [load]);

  return (
    <div className="screen signals-screen">
      <h2 className="watchlist-box-title">{t("models.title")}</h2>
      <p className="signals-count">{t("models.subtitle")}</p>

      {error && (
        <div className="state-msg">
          <p>{t("models.error")}</p>
          <button type="button" onClick={load}>
            {t("signals.retry")}
          </button>
        </div>
      )}
      {!error && !entries && <p className="chart-loading">{t("models.loading")}</p>}
      {entries && entries.length === 0 && <p className="watchlist-box-empty">{t("models.error")}</p>}
      {entries?.map((entry) => (
        <ModelCard key={entry.id} entry={entry} />
      ))}
    </div>
  );
}
