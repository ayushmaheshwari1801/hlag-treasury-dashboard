// Sticky bar: the selected scenario and its key liquidity figures, so a treasurer never scrolls back to the top.
// Layout from the uploaded frontend; every figure is read from the dashboard run (POST /api/v1/dashboard) and only
// formatted here. The time horizon moved here from the old header.
import { eur, num, when } from "../format";
import { riskStatus } from "../Insights";
import type { MarketRisk } from "./useMarketRisk";
import type { RiskView } from "./MarketCards";

export const STEPS = [
  { id: "position", label: "Liquidity forecast" },
  { id: "signals", label: "Market · News · Impact · Actions" },
] as const;

/** Name of the scenario the page is showing: the applied News forecast, engine preset, manual sliders or last week's moves repeated. */
export function scenarioLabel(r: MarketRisk) {
  // Sliders moved on top of a News forecast or preset override those drivers only (see the What-if note).
  const adjusted = Object.keys(r.shocks).length ? " · sliders adjusted" : "";
  if (r.forecastActive && r.forecast) return (r.forecast.scenario?.name ?? "News Event scenario") + adjusted;
  if (r.activePreset) return (r.activePreset.name ?? "Engine preset") + adjusted;
  return adjusted ? "Manual scenario" : "Last week's moves repeated";
}

export function ContextBar({ r, v, onSources }: { r: MarketRisk; v: RiskView | null; onSources: () => void }) {
  const f = v?.f, lq = v?.lq, ci = v?.ci;
  const calc = f?.calculation ?? f?.cash_engine;
  const status = f ? riskStatus(lq, calc) : null;
  const minimum = lq ? lq.cash_t0?.reserve_eur_m ?? lq.minimum_eur_m : null;
  const end = calc?.horizon_end;
  return <div className="context-bar" role="region" aria-label="Scenario summary">
    <div className="context-main">
      <div className="context-forecast"><span>Scenario</span>
        <b>{scenarioLabel(r)}{r.busy && <span className="muted small"> · recalculating…</span>}</b>
        {r.error && <small role="status">Calculation unavailable. Figures below are from the last completed scenario.</small>}
        <small>{f ? `${when(f.as_of, false)}${end ? ` → ${when(end, false)}` : ""} · updated ${when(f.as_of)}` : ""}</small></div>
      {f && <div className="context-kpis">
        <div><span>Lowest liquidity</span><b>€{num(lq.scenario_eur_m, 0)}m</b><small>{lq.scenario_min_date ? `on ${when(lq.scenario_min_date, false)}` : "date unavailable"}</small></div>
        <div><span>Headroom above minimum</span><b className={lq.scenario_headroom_eur_m < 0 ? "neg" : ""}>€{num(lq.scenario_headroom_eur_m, 0)}m</b><small>minimum reserve €{num(minimum, 0)}m</small></div>
        <div><span>Risk status</span><b className={status?.tone === "bad" ? "neg" : ""}>{status?.label}</b><small>{status?.incomplete ? "inputs incomplete" : `first breach: ${status?.firstBreach}`}</small></div>
        <div><span>Total estimated impact</span><b className={ci.adverse_total_eur_m < 0 ? "neg" : ""}>{eur(ci.adverse_total_eur_m)}</b><small>adverse rows · net {eur(ci.net_eur_m)}</small></div>
        <label className="context-delta context-horizon"><span>Time horizon</span>
          <select value={r.months} onChange={e => r.setMonths(Number(e.target.value))} aria-label="Time horizon">
            {f.horizons.map((h: any) => <option key={h.months} value={h.months}>Next {h.months} months</option>)}
          </select></label>
      </div>}
      {!f && <p className="small muted" role="status">{r.error ? r.error : "Loading the dashboard…"}</p>}
    </div>
    <nav className="context-steps" aria-label="Dashboard steps">
      {STEPS.map((s, i) => <a key={s.id} href={`#${s.id}`}><span>{i + 1}</span>{s.label}</a>)}
      <button className="ghost" onClick={onSources} title="Data connections">Data connections</button>
    </nav>
  </div>;
}
