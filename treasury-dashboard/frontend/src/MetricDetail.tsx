import { assumptionExplanation, treasuryText } from "./treasuryCopy";
import { useEffect, useState } from "react";
import { api } from "./api";
import { Drawer } from "./Drawer";
import { LineChart } from "./charts";
import { eur, metricValue, metricChange, ROW_LABEL } from "./format";
import { assumptionName, assumptionText, sliderSpec } from "./engineUi";
import type { MetricAssumption, ModelAssumptionGroup } from "./dashboard/metricAssumptions";

// Every displayed figure belongs to the dashboard's current cash-engine calculation.
export default function MetricDetail({ rowId, finance, busy, onClose, onCashDetail, explain, group, overrides, onOverride, newsDrivers }: {
  rowId: string; finance: any; busy: boolean; onClose: () => void; onCashDetail: () => void;
  explain?: string; group?: ModelAssumptionGroup; overrides: Record<string, number | boolean | string>;
  onOverride: (key: string, value: number | boolean | string | null) => void; newsDrivers: MetricAssumption[];
}) {
  const [range, setRange] = useState("3M"), [history, setHistory] = useState<any>(null), [error, setError] = useState("");
  const metric = finance.market_metrics.find((m: any) => m.id === rowId);
  const row = finance.cash_impact.rows.find((r: any) => r.id === rowId);
  const asOf = String(finance.as_of).slice(0, 10);
  useEffect(() => {
    let active = true; setHistory(null); setError("");
    if (metric?.series_id) api.marketHistory(metric.series_id, asOf, range)
      .then(result => { if (active) setHistory(result); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [metric?.series_id, asOf, range]);
  return <Drawer title={`${metric?.name ?? ROW_LABEL[rowId]} · market and cash detail`} onClose={onClose} wide modal>
    <p>{explain}</p>
    {busy && <p role="status">Recalculating the selected scenario… Values below are from the last completed run.</p>}
    {metric && <section className="dx-box"><h3>Market snapshot</h3><b>{metricValue(metric.id, metric.value)} {metric.id.startsWith("fx_") ? metric.unit : ""}</b>
      <p>1-week change {metricChange(metric)} · {metric.date} · {treasuryText(metric.source)} · {metric.freshness}</p></section>}
    <section className="dx-box"><h3>Selected scenario · cash impact</h3><b>{eur(row?.impact_eur_m)}</b>
      <p>{row?.memo_only_reason ?? row?.memo_only ?? ""}</p>
      {rowId === "fx_gbp" && <p>No GBP cash flows modelled. Balance-sheet translation is a memo, not cash.</p>}
      <p>{finance.scenario?.full_scenario?.name} · through {finance.calculation?.horizon_end}</p>
      {!row?.memo_only && <button className="primary" onClick={onCashDetail} disabled={busy}>View native cash-flow detail</button>}</section>
    {!!group?.params.length && <section className="metric-assumptions"><h3>Model assumptions · {group?.title ?? "this metric"}</h3>
      <p className="small muted">Edits apply only to this scenario and are shared with the assistant and recommendation agent.</p>
      {group?.params.length ? <table className="t asm-table"><tbody>{group.params.map((p: any) => {
        const value = overrides[p.key] ?? p.value, changed = p.key in overrides && value !== p.value, spec = sliderSpec(p);
        return <tr key={p.key}><td><b>{assumptionName(p.key)}</b><div className="small muted">{assumptionExplanation(p.rationale)}</div></td>
          <td>{spec ? <input type="range" min={spec.min} max={spec.max} step={spec.step} value={Number(value)} aria-label={assumptionName(p.key)} onChange={e => onOverride(p.key, Number(e.target.value))} />
            : typeof value === "boolean" ? <input type="checkbox" checked={value} aria-label={assumptionName(p.key)} onChange={e => onOverride(p.key, e.target.checked)} /> : null}
            <b>{assumptionText(value, p.unit, p.key)}</b>{changed && <button className="link" onClick={() => onOverride(p.key, null)}>profile {assumptionText(p.value, p.unit, p.key)}</button>}</td></tr>;
      })}</tbody></table> : <p>No editable assumption for this metric.</p>}
    </section>}
    {metric && <section><h3>Market history</h3><div className="seg">{["1M", "3M", "6M", "1Y", "All"].map(r => <button key={r} className={range === r ? "on" : ""} aria-pressed={range === r} onClick={() => setRange(r)}>{r}</button>)}</div>
      {error ? <p role="alert">{error}</p> : history ? <LineChart points={history.points} unit={metric.unit} /> : <p role="status">Loading history…</p>}
      <p className="small muted">Same market store and as-of date as the dashboard. Missing observations are not invented.</p></section>}
    {!!newsDrivers.length && <section><h3>Independent news estimates</h3><p className="small muted">These assumptions are not applied to the selected What-if scenario.</p><ul>{newsDrivers.map((d, i) => <li key={i}>{d.label}: {d.value}</li>)}</ul></section>}
  </Drawer>;
}
