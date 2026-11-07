import { assumptionExplanation } from "../treasuryCopy";
// Working capital: when cash lands (roadmap phase 3), migrated from the unified source's Working Capital view.
// The "collections slower by N days" lever is sent as the what-if shock collection_delay, which the gateway maps to the
// spec's scenario field freight.transit_delay_days on every trade (6.5, D40). Lags come from the engine assumptions,
// the freight and fuel cash lines from GET /api/v1/engine/runs/{run_id}/cashflows; the browser only formats them.
import { useEffect, useState } from "react";
import { api } from "../api";
import { Drawer } from "../Drawer";
import { eur, num, when } from "../format";
import type { MarketRisk } from "./useMarketRisk";
import type { RiskView } from "./MarketCards";

const LAG_KEYS = ["freight.collection_lag_days", "bunker.supplier_lag_days", "freight.delay_affected_share"];
const LAG_NAME: Record<string, string> = { "freight.collection_lag_days": "Collection lag (DSO)", "bunker.supplier_lag_days": "Fuel supplier payment lag (DPO)",
  "freight.delay_affected_share": "Revenue collected later when transit is delayed" };
const tone = (v: number | null | undefined) => v == null ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "";

function useCashflows(runId?: string) {
  const [d, setD] = useState<any>(null), [err, setErr] = useState("");
  useEffect(() => {
    if (!runId) return; let live = true; setErr("");
    api.engineCashflows(runId).then(r => { if (live) setD(r); }).catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [runId]);
  return { d, err };
}

export function WorkingCapitalCard({ r, v, params }: { r: MarketRisk; v: RiskView; params: any[] | null }) {
  const f = v.f, calc = f.calculation, lq = v.lq, rate = v.rate;
  const delay = r.shocks.collection_delay ?? 0;
  const onDelay = (days: number | null) => r.setShock("collection_delay", days);
  const { d, err } = useCashflows(calc?.run_id);
  const [draft, setDraft] = useState(delay);
  const [timingOpen, setTimingOpen] = useState(false);
  useEffect(() => setDraft(delay), [delay]);
  const lags = (params ?? []).filter((p: any) => LAG_KEYS.includes(p.key)).sort((x: any, y: any) => LAG_KEYS.indexOf(x.key) - LAG_KEYS.indexOf(y.key));
  const dso = lags.find((p: any) => p.key === "freight.collection_lag_days")?.value;
  const rows: any[] = d?.by_driver_month ?? [];
  const end = calc?.horizon_end ? String(calc.horizon_end).slice(0, 7) : null;   // the selected time horizon
  const months: string[] = Array.from(new Set<string>(rows.map((x: any) => x.month))).filter(m => !end || m <= end).sort();
  const cell = (driver: string, m: string, k: string) => rows.find((x: any) => x.driver === driver && x.month === m)?.[k];
  const status = calc?.engine_status ?? calc?.status;
  const commit = () => { if (draft !== delay) onDelay(draft || null); };

  return <section className={`card working-capital ${delay ? "custom" : ""}`} aria-labelledby="wc-title">
    <h2 id="wc-title">Working Capital</h2>
    <div className="wc-stack">
      <div className="hd"><h3 id="wcl">Collections timing what-if</h3>{delay > 0 && <button className="link small" onClick={() => onDelay(null)}>Reset to normal timing</button>}</div>
      <p className="sub">Customers pay later than normal: every trade's freight and fuel-surcharge (MFR) receipts arrive N days after the usual collection lag. Applies on top of the selected scenario.</p>
      <div className="wc-lever">
        <label htmlFor="wc-delay">Collections slower by <b>{draft} days</b>{dso != null && <span className="muted"> · effective collection lag {num(dso + draft, 0)} days</span>}</label>
        <input id="wc-delay" type="range" min={0} max={60} step={1} value={draft} onChange={e => setDraft(Number(e.target.value))}
          onPointerUp={commit} onKeyUp={commit} onBlur={commit} aria-valuetext={`${draft} days`} />
        <div className="sl-foot small muted"><span>0 · normal</span><span>+60 days</span></div>
      </div>
      <div className="x-tiles">
        <div className="x-tile"><span>Effect on cash over the horizon</span><b>{calc?.attribution_usd_m?.total != null ? eur(calc.attribution_usd_m.total / rate) : "—"}</b><em>Net scenario change, all drivers{r.busy ? " · recalculating…" : ""}</em></div>
        <div className="x-tile"><span>Projected low point</span><b>€{num(lq.scenario_headroom_eur_m, 0)}m <small>headroom</small></b><em>{lq.scenario_min_date ? `On ${when(lq.scenario_min_date, false)}` : ""}</em></div>
        <div className="x-tile"><span>Status</span><b><span className={`chip st-${String(status ?? "").toLowerCase()}`}>{status ?? "—"}</span></b><em>{calc?.first_breach_date ? `Reserve breached from ${when(calc.first_breach_date, false)}` : "No reserve breach in the horizon"}</em></div>
      </div>
      <p className="foot">The collection delay applies to all freight revenue by default. Receipts move to later periods, and any pushed past the horizon end leave it, so a lasting slowdown is a cash drag in the horizon.</p>
    </div>

    {/* Timing detail opens in a modal, so the card keeps its height and the two board columns stay aligned. */}
    <button className="wc-timing-open" onClick={() => setTimingOpen(true)} aria-haspopup="dialog">
      <span><b>Working capital timing</b><small>Collection and fuel payment lags · freight and fuel cash by month</small></span><span aria-hidden>›</span>
    </button>
    {timingOpen && <Drawer title="Working capital timing" modal wide onClose={() => setTimingOpen(false)}>
      <p className="sub">Freight is collected after the collection lag; fuel is paid after the supplier lag. A price shock today reaches the bank only when those invoices settle.</p>
      <div className="x-tiles">{lags.map((p: any) => <div key={p.key} className="x-tile"><span>{LAG_NAME[p.key]}</span>
        <b>{p.unit === "fraction" ? `${num(p.value * 100, 0)}%` : `${num(p.value, 1)} days`} <small className="chip">{p.label}</small></b><em>{assumptionExplanation(p.rationale)}</em></div>)}
        {params === null && <p className="small muted">The timing assumptions are not available.</p>}</div>
      {!calc?.run_id && <p className="small muted">Monthly freight and fuel cash flows are unavailable.</p>}
      {err && <p className="err small">{err}</p>}
      {months.length > 0 && <div className="cash-detail-table cf-table"><table>
        <thead><tr><th>Month</th><th className="r">Freight receipts · baseline</th><th className="r">Freight · scenario change</th><th className="r">Fuel payments · baseline</th><th className="r">Fuel · scenario change</th></tr></thead>
        <tbody>{months.map(m => { const fb = cell("freight", m, "baseline_usd_m"), fd = cell("freight", m, "delta_usd_m"), bb = cell("bunker", m, "baseline_usd_m"), bd = cell("bunker", m, "delta_usd_m");
          return <tr key={m}><td>{m}</td><td className="r">{fb == null ? "—" : num(fb / rate, 0)}</td><td className={`r ${tone(fd)}`}>{fd == null ? "—" : eur(fd / rate)}</td>
            <td className="r">{bb == null ? "—" : num(bb / rate, 0)}</td><td className={`r ${tone(bd)}`}>{bd == null ? "—" : eur(bd / rate)}</td></tr>; })}</tbody></table></div>}
      <p className="foot">Gross freight and fuel cash lines in EUR million at the analysis-date exchange rate, shown for timing. Their baseline amounts are already inside the operating cash-flow run-rate, so only the scenario change moves the liquidity path (no double counting). The forecast includes new shipments and purchases from the analysis date, so receipts start one collection lag later; invoices raised before T0 are collected inside the run-rate, which is why the first months show only part of normal receipts. Fuel includes the marine fuel recovery (MFR) collected with freight.</p>

    </Drawer>}
  </section>;
}
