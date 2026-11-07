// Row Methodology view: how one Estimated Cash-Flow Impact row was built. Every text, formula, component,
// assumption, source and playbook entry comes from hlag_engine through POST /api/v1/engine/methodology; the
// sensitivity table is five real engine runs (POST /api/v1/engine/sensitivity). Nothing here is hard-coded content.
import { Fragment, useEffect, useState } from "react";
import { api } from "./api";
import { Drawer } from "./Drawer";
import { LineChart } from "./charts";
import { CashLinesTable, ReviewSection } from "./CashDetails";
import { eur, shockText, when } from "./format";
import { assumptionText } from "./engineUi";
import { type CurrentFigure, historySlice, overrideText, sensitivityKey, sensitivityRows, seriesChangeText, seriesValueText, stepLabel, symbolValueText } from "./methodologyUi";

const sensitivityCache = new Map<string, any>();
const PROV_CLASS: Record<string, string> = { MARKET: "ok", DISCLOSED: "ok", DERIVED: "", ASSUMPTION: "warn", PROXY: "warn", UNAVAILABLE: "bad" };

function Prov({ label, confirmed, stale }: { label?: string | null; confirmed?: boolean | null; stale?: boolean | null }) {
  if (!label) return null;
  return <><span className={`chip ${PROV_CLASS[label] ?? ""}`}>{label}</span>{confirmed === false && <span className="chip warn">unconfirmed</span>}{stale && <span className="chip warn">stale</span>}</>;
}

type Props = {
  rowId: string; runId: string; months: number; busy: boolean;
  slider?: any; shock?: number; onShock: (id: string, v: number | null) => void;
  request: Record<string, unknown>; onApply: () => void; onClose: () => void;
  /** The card's figures for this row under the current scenario (null while the page recalculates). */
  currentFigure?: CurrentFigure | null;
};

export default function Methodology({ rowId, runId, months, busy, slider, shock, onShock, request, onApply, onClose, currentFigure }: Props) {
  const [m, setM] = useState<any>(null), [err, setErr] = useState("");
  const [series, setSeries] = useState<string>(""), [range, setRange] = useState<string>("");
  const [lines, setLines] = useState(false);
  useEffect(() => {
    let live = true; setErr("");
    api.engineMethodology(rowId, runId, months).then(r => { if (live) setM(r); }).catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [rowId, runId, months]);
  const header = m?.header;
  const selected = header?.series.find((s: any) => s.series_id === series) ?? header?.series.find((s: any) => s.series_id === header.default) ?? header?.series[0];
  const hist = selected ? header.history[selected.series_id] : null;
  const ranges: string[] = hist?.ranges ?? [];
  const activeRange = ranges.includes(range) ? range : ranges[ranges.length - 1];
  const value = slider ? (shock ?? slider.value ?? 0) : null;
  // The driver's slider, shared with What-if: in the Sensitivity section (freight, bunker) or the Scenario box (rates, FX).
  const sliderBox = slider ? <div className={`slider ${shock != null ? "set" : ""}`}>
    <div className="sl-top"><label htmlFor={`m-${rowId}`}>{slider.label}</label><span className="sl-val">{shockText(value, slider.unit)}</span></div>
    <div className="sl-track"><input id={`m-${rowId}`} type="range" min={slider.min} max={slider.max} step={slider.step} value={value ?? 0}
      onChange={e => onShock(rowId, Number(e.target.value))} aria-valuetext={shockText(value, slider.unit)} /></div>
    <div className="sl-foot"><span>{shockText(slider.min, slider.unit)}</span>
      {shock != null ? <button className="link" onClick={() => onShock(rowId, null)}>reset</button> : <span className="muted">current scenario</span>}
      <span>{shockText(slider.max, slider.unit)}</span></div>
    <p className="small muted">Same setting as the What-if slider; moving either moves both.{busy ? " Recalculating…" : ""}</p>
  </div> : <p className="small muted">No slider for this row.</p>;

  return (
    <Drawer title={m ? `${m.label} · methodology` : "Methodology"} onClose={onClose} modal wide>
      {err && <p className="err" role="alert">{err}</p>}
      {!m && !err && <p className="muted">Loading the engine's methodology…</p>}
      {m && <div className={`method ${busy ? "is-busy" : ""}`}>
        {/* ---- header: driver series, latest value, 1-week change, provenance, date, source */}
        <section className="mh">
          <div className="mh-pick">
            {header.series.length > 1 && (m.row_id === "freight"
              ? <label className="small">Market series <select value={selected.series_id} onChange={e => setSeries(e.target.value)}>
                  {header.series.map((s: any) => <option key={s.series_id} value={s.series_id}>{s.trade_name ? `${s.trade_name}: ${s.name}` : s.name}</option>)}</select></label>
              : <div className="presets" role="tablist" aria-label="Market series">{header.series.map((s: any) =>
                  <button key={s.series_id} role="tab" aria-selected={s.series_id === selected.series_id} className={`preset ${s.series_id === selected.series_id ? "on" : ""}`} onClick={() => setSeries(s.series_id)}>{s.name}</button>)}</div>)}
          </div>
          <div className="mh-main">
            <div><div className="small muted">{selected.name}</div><div className="mh-val">{seriesValueText(selected)}</div></div>
            <div><div className="small muted">1-week change</div><div className={`mh-chg ${(selected.change ?? 0) > 0 ? "up" : (selected.change ?? 0) < 0 ? "down" : ""}`}>{seriesChangeText(selected)}</div>
              {selected.comparison_date && <div className="small muted">vs {when(selected.comparison_date, false)}</div>}</div>
            <div><div className="small muted">Provenance</div><Prov label={selected.provenance} confirmed={selected.confirmed} stale={selected.stale} /></div>
            <div><div className="small muted">Published</div><b>{when(selected.date, false)}</b></div>
            <div className="mh-src"><div className="small muted">Source</div><span>{selected.source ?? "—"}</span></div>
          </div>
          {selected.weights && <p className="small muted">Weights (H1 2026 volumes): {Object.entries(selected.weights).map(([t, w]: any) => `${t.replace(/_/g, " ").toLowerCase()} ${(w * 100).toFixed(1)}%`).join(" · ")}. Africa & Intraregional uses the Drewry composite as its proxy, as in the calculation.</p>}
          {m.full && hist && <div className="mh-chart">
            <div className="presets" role="tablist" aria-label="History range">{ranges.map(r =>
              <button key={r} role="tab" aria-selected={r === activeRange} className={`preset ${r === activeRange ? "on" : ""}`} onClick={() => setRange(r)}>{r}</button>)}
              <span className="small muted">History from {when(hist.first, false)}; only ranges this history covers are offered.</span></div>
            <LineChart points={historySlice(hist.points, activeRange)} unit={selected.unit ?? ""} />
          </div>}
        </section>

        {/* ---- how this figure was built: engine components, inside vs after the selected horizon */}
        <section>
          <h3>How this figure was built</h3>
          <Breakdown b={m.breakdown} />
          <p className="small muted">Direct cash flows (one-off payments from News, such as fines) are shown on their own row and are not part of these drivers.</p>
        </section>

        {/* ---- sensitivity: the driver's slider (shared with What-if) and five real engine runs (freight, bunker) */}
        {m.full ? <section>
          <h3>Sensitivity</h3>
          {sliderBox}
          <Sensitivity rowId={rowId} request={request} current={value} unit={slider?.unit} figure={busy ? null : currentFigure ?? null} onUse={v => onShock(rowId, v)} />
        </section>
          : slider && <section>{/* rates and FX: no sensitivity table; the slider keeps its place */}
          <h3>Scenario</h3>
          {sliderBox}
        </section>}

        {/* ---- how it works: engine text, formulas with every symbol and its current value */}
        <section>
          <h3>How it works</h3>
          {m.summary.map((s: string, i: number) => <p key={i} className="m-p">{s}</p>)}
          {m.formulas.map((f: any) => <Formula key={f.name} f={f} />)}
          <Formula f={m.approximation} approx />
        </section>

        {/* ---- treasury implications: the engine's playbook entries for this driver */}
        <section>
          <h3>Treasury implications</h3>
          <p className="small muted">The engine's playbook for this driver (deterministic, not AI-generated). Review prompts only; nothing is executed.</p>
          {m.playbook.map((p: any) => <div key={p.entry_id} className="m-play"><div className="small muted">When: {p.trigger_type.replace("_", " ")} {p.trigger}{p.hedge_tool ? ` · hedge tool target ${p.hedge_tool}` : ""}</div>
            <ol>{p.actions.map((a: string) => <li key={a}>{a}</li>)}</ol></div>)}
        </section>

        <section>
          <h3>Cash lines</h3>
          <p className="small">{m.lines_included}</p>
          <p className="small muted">Cash lines dated on or before T0 are not included.</p>
          <button className="preset" onClick={() => setLines(!lines)} aria-expanded={lines}>{lines ? "Hide cash lines" : "Show cash lines"}</button>
          {lines && <CashLinesTable runId={runId} rowId={rowId} horizonEnd={m.breakdown.horizon_end} />}
        </section>

        {/* ---- assumptions and sources */}
        <section>
          <h3>Assumptions & sources</h3>
          <div className="cash-detail-table"><table><thead><tr><th>Assumption</th><th>Used for</th><th>Value</th><th>Provenance</th><th>Override</th></tr></thead>
            <tbody>{m.assumptions.map((a: any) => <tr key={a.key} className={a.changed ? "chg" : ""}><td>{a.name}<small>{a.key}</small></td><td>{a.used_for}</td>
              <td>{assumptionText(a.value, a.unit, a.key)}{a.unit && <small>{a.unit}</small>}</td><td><Prov label={a.label} />{a.source && <small>{a.source}</small>}</td><td>{overrideText(a)}</td></tr>)}</tbody></table></div>
          <h4>Market inputs</h4>
          <div className="cash-detail-table"><table><thead><tr><th>Series</th><th>Used for</th><th>Value at T0</th><th>Date</th><th>Provenance</th><th>Source</th></tr></thead>
            <tbody>{m.market_inputs.map((x: any) => <tr key={x.series_id}><td>{x.name}<small>{x.series_id}</small></td><td>{x.role}</td><td>{seriesValueText(x)}</td><td>{x.date ?? "—"}</td><td><Prov label={x.label} confirmed={x.confirmed} stale={x.stale} /></td><td>{x.source}</td></tr>)}</tbody></table></div>
          {m.disclosed.length > 0 && <><h4>Disclosed figures</h4>
            <div className="cash-detail-table"><table><thead><tr><th>Figure</th><th>Used for</th><th>Value</th><th>Source</th></tr></thead>
              <tbody>{m.disclosed.map((a: any) => <tr key={a.key}><td>{a.name}<small>{a.key}</small></td><td>{a.used_for}</td><td>{assumptionText(a.value, a.unit, a.key)}</td><td>{a.source}</td></tr>)}</tbody></table></div></>}
        </section>

        <section>
          <h3>Not included / limitations</h3>
          <ul className="m-list">{m.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
          {m.memo && <p className="small muted">Translation memo (USD, not cash, not on the liquidity path): {Object.entries(m.memo.positions_usd_m).map(([c, v]: any) => `${c} position USD ${v.toLocaleString("en-US")}m, revaluation USD ${(m.memo.revaluation_usd_m[c] ?? 0).toFixed(2)}m at ${m.memo.horizon_end}`).join("; ")}.</p>}
        </section>

        <ReviewSection runId={runId} />

        <div className="m-foot"><span className="small muted">Run {m.run_id} · profile {m.profile_version} · as of {m.as_of} · read-only</span>
          <button className="primary" onClick={onApply}>Apply scenario to dashboard</button></div>
      </div>}
    </Drawer>
  );
}

function Breakdown({ b }: { b: any }) {
  const [open, setOpen] = useState<string | null>(null);
  const t = b.totals_eur_m;
  return <>
    <div className="cash-detail-table"><table className="m-bd"><thead><tr><th>Component</th><th className="r">Inside horizon<small>to {b.horizon_end}</small></th><th className="r">After it<small>to {b.engine_horizon_end}</small></th><th className="r">Beyond engine<small>not on path</small></th></tr></thead>
      <tbody>{b.components.map((c: any) => <Fragment key={c.id}>
        <tr className={c.details.length ? "clickable" : ""} onClick={() => c.details.length && setOpen(open === c.id ? null : c.id)}>
          <td>{c.details.length > 0 && <span aria-hidden>{open === c.id ? "▾ " : "▸ "}</span>}{c.label}<small>{c.description}</small></td>
          <td className={`r b ${c.inside_eur_m < 0 ? "neg" : c.inside_eur_m > 0 ? "pos" : ""}`}>{c.inside_eur_m === 0 ? "none" : eur(c.inside_eur_m, 2)}</td>
          <td className="r">{eur(c.after_eur_m, 2)}</td><td className="r">{eur(c.beyond_eur_m, 2)}</td></tr>
        {open === c.id && c.details.map((d: any) => <tr key={c.id + d.detail} className="sub"><td>{d.detail}</td><td className="r">{eur(d.inside_eur_m, 2)}</td><td className="r">{eur(d.after_eur_m, 2)}</td><td className="r">{eur(d.beyond_eur_m, 2)}</td></tr>)}
      </Fragment>)}</tbody>
      <tfoot><tr><td>Row total</td><td className="r b">{eur(t.inside, 2)}</td><td className="r">{eur(t.after, 2)}</td><td className="r">{eur(t.beyond, 2)}</td></tr></tfoot></table></div>
    <p className="small muted">{b.note} EUR at the T0 rate (USD {b.eurusd} per EUR). {b.beyond_horizon_note}</p>
  </>;
}

function Formula({ f, approx }: { f: any; approx?: boolean }) {
  return <div className={`m-formula ${approx ? "approx" : ""}`}>
    <div className="m-fname">{f.name}{approx && <span className="chip warn">{f.label}</span>}</div>
    <code>{f.expression}</code>
    {f.symbols.length > 0 && <table className="m-sym"><tbody>{f.symbols.map((s: any) => <tr key={s.symbol}><td><code>{s.symbol}</code></td><td>{s.meaning}</td>
      <td>{symbolValueText(s)}</td><td className="small muted">{s.source ?? ""}</td></tr>)}</tbody></table>}
  </div>;
}

function Sensitivity({ rowId, request, current, unit, figure, onUse }: { rowId: string; request: Record<string, unknown>; current: number | null; unit?: string; figure: CurrentFigure | null; onUse: (v: number) => void }) {
  const key = sensitivityKey(rowId, request);
  const [d, setD] = useState<any>(() => sensitivityCache.get(key) ?? null), [err, setErr] = useState("");
  useEffect(() => {
    const hit = sensitivityCache.get(key);
    if (hit) { setD(hit); return; }
    let live = true; setD(null); setErr("");
    api.engineSensitivity({ ...request, row_id: rowId }).then(r => { sensitivityCache.set(key, r); if (live) setD(r); }).catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [key]);
  const rows = d ? sensitivityRows(d.points, current, figure) : [];
  const currentRow = rows.find(p => p.kind === "current");
  const tone = (v: number | null | undefined) => v == null ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "";
  return <div className="m-sens">
    {err && <p className="err small">{err}</p>}
    {!d && !err && <p className="small muted" role="status">Running 5 engine runs (about 5 seconds)…</p>}
    {d && <><table className="m-bd m-sens-table"><thead><tr><th>Driver move</th><th className="r">Row impact (EUR m)</th><th className="r">Min headroom change (EUR m)</th><th /></tr></thead>
      <tbody>{rows.map(p => p.kind === "current"
        ? <tr key="current" className="here current"><td>Current: {shockText(p.slider_value, unit ?? d.slider_unit)} <span className="small muted">(current scenario)</span></td>
            <td className={`r b ${tone(p.impact_eur_m)}`}>{eur(p.impact_eur_m, 1)}</td><td className="r">{eur(p.headroom_change_eur_m, 1)}</td><td /></tr>
        : <tr key={p.step_pct} className={p.here ? "here" : ""}>
            <td>{stepLabel(p, d.slider_unit)}{p.here && <span className="chip">current</span>}</td>
            <td className={`r b ${tone(p.impact_eur_m)}`}>{eur(p.impact_eur_m, 1)}</td>
            <td className="r">{eur(p.headroom_change_eur_m, 1)}</td>
            <td className="r"><button className="preset m-use" onClick={() => onUse(p.slider_value)}>Use this shock</button></td></tr>)}</tbody></table>
      <p className="small muted">{d.basis} {d.note}{d.cached ? " (cached result)" : ""} The minimum headroom is set by the lowest point of the liquidity path; moves whose cash lands after that point do not change it.</p>
      {currentRow && currentRow.headroom_change_eur_m == null && <p className="small muted">Current row: the minimum headroom change is not available for this run, so it is shown as "—".</p>}</>}
  </div>;
}
