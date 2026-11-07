// Panels for the hlag_engine endpoints: run status, all market series (with the explicit refresh), cash flows and
// per-run assumption overrides. Added alongside the original layout; nothing here changes the engine profile.
import { useEffect, useState } from "react";
import { api } from "./api";
import { PathChart } from "./charts";
import { eur, num, when } from "./format";
import { assumptionName, assumptionText, changedOverrides, clampTo, deskExceptionText, eurWithUsd, hedgeMonths, groupAssumptions, groupSeries, notFetchedLive, pivotCashflows, sliderSpec, toEur } from "./engineUi";

const DRIVER: Record<string, string> = { freight: "Freight", bunker: "Bunker", rates: "Rates", fx: "FX", direct: "Direct cash" };
const FLAG: Record<string, string> = { synthetic_register: "Illustrative exposures", proxy_based: "proxy-based", uses_unconfirmed_data: "unconfirmed data",
  includes_unapproved_adjustments: "unapproved adjustments", incomplete_data: "incomplete data" };
const text = (x: any) => typeof x === "string" ? x : x?.text ?? x?.reason ?? x?.detail ?? JSON.stringify(x);

// ---------------------------------------------------------------- 4. run status, desk exceptions, flags

const DRIVER_ROWS: Record<string, string[]> = { freight: ["freight"], bunker: ["bunker"], rates: ["interest"], fx: ["fx_eur", "fx_gbp", "fx_cny", "fx_cad"] };

/** The driver's figure on the cash-impact card (selected horizon), e.g. "−€71.5m"; "not available" if a row is incomplete. */
function cardImpact(driver: string, rows: any[] | undefined): string | null {
  const ids = DRIVER_ROWS[driver]; if (!ids || !rows) return null;
  const vals = ids.map(id => rows.find(r => r.id === id)?.impact_eur_m);
  return vals.some(v => v == null) ? "not available" : eur(vals.reduce((a, b) => a + b, 0));
}

export function RunStatus({ calc, rate, months, rows }: { calc: any; rate: number; months?: number; rows?: any[] }) {
  if (!calc) return null;
  const status = calc.engine_status ?? calc.status;
  const desk: any[] = calc.desk_exceptions ?? [];
  const flags = Object.entries(calc.flags ?? {}).filter(([k, v]) => v === true && FLAG[k]);
  const reasons: any[] = calc.status_reasons ?? [];
  return (
    <div className={`runstatus ${desk.length ? "has-desk" : ""}`} role="status">
      <div className="rs-top">
        <span className="rs-label">{calc.engine_status ? "Calculation status" : "Calculation status"}</span>
        <span className={`chip st-${String(status).toLowerCase()}`}>{status ?? "—"}</span>
        {flags.map(([k]) => <span key={k} className={`chip ${k === "synthetic_register" ? "" : "warn"}`}>{FLAG[k]}</span>)}
      </div>
      {reasons.length > 0 && <ul className="rs-reasons small">{reasons.map((r, i) => <li key={i}>{text(r)}</li>)}</ul>}
      {calc.engine_status !== undefined && (desk.length === 0
        ? <div className="rs-desk none small">No desk exceptions raised</div>
        : <ul className="rs-desk">{desk.map((e, i) => { const d = deskExceptionText(e, rate, calc.engine_horizon_end, { months, cardImpact: e.kind === "driver" ? cardImpact(e.driver, rows) : null }); return <li key={i}>
            <b>{d.label}:</b>{" "}
            <span data-tip={d.usdTip || undefined}>{d.text}</span>
            {d.measure && <div className="small muted">{d.measure}</div>}
            {e.detail && <div className="small muted">{e.detail}</div>}</li>; })}</ul>)}
    </div>
  );
}

// ---------------------------------------------------------------- 1 and 6. all market series, explicit refresh

const SERIES_NAME: Record<string, string> = {
  FX_USD_PER_EUR: "EUR / USD", FX_USD_PER_GBP: "GBP / USD", FX_USD_PER_CNY: "CNY / USD", FX_USD_PER_CAD: "CAD / USD",
  FX_USD_PER_INR: "INR / USD", FX_USD_PER_BRL: "BRL / USD", ESTR: "€STR", SOFR: "SOFR", UST_2Y: "US Treasury 2Y", EA_2Y: "Euro area 2Y (AAA)",
  BB_OAS: "BB high-yield OAS", BRENT: "Brent crude", VLSFO_SIN: "VLSFO Singapore", VLSFO_RTM: "VLSFO Rotterdam", HSFO_SIN: "HSFO Singapore",
  HSFO_RTM: "HSFO Rotterdam", WCI_COMPOSITE: "WCI composite", WCI_SHA_RTM: "WCI Shanghai–Rotterdam", WCI_SHA_LAX: "WCI Shanghai–Los Angeles",
  WCI_RTM_NYC: "WCI Rotterdam–New York",
};

function seriesValue(s: any) {
  if (s.value == null) return "—";
  const v = s.value as number;
  const d = s.series_id.startsWith("FX_") ? (v < 0.1 ? 5 : 4) : s.unit === "%" ? 3 : v >= 100 ? 0 : 2;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function seriesChange(s: any) {
  if (s.weekly_change == null) return "—";
  const c = s.weekly_change as number, sign = c > 0 ? "+" : c < 0 ? "−" : "";
  if (s.weekly_change_unit === "bp") return `${sign}${Math.abs(c).toFixed(1)} bp`;
  return s.weekly_change_pct == null ? `${sign}${Math.abs(c).toPrecision(3)}` : `${sign}${Math.abs(s.weekly_change_pct).toFixed(2)}%`;
}

export function MarketGrid({ onRefreshed, asOf }: { onRefreshed: () => void; asOf?: string }) {
  const [d, setD] = useState<any>(null), [err, setErr] = useState("");
  const [actor, setActor] = useState(""), [busy, setBusy] = useState(false);
  const [report, setReport] = useState<any>(null), [refreshErr, setRefreshErr] = useState("");
  const load = () => api.engineMarket(asOf).then(r => { setD(r); setErr(""); }).catch(e => setErr(e.message));
  useEffect(() => { void load(); }, [asOf]);
  const refresh = async () => {
    if (busy || !actor.trim()) return;
    if (!window.confirm(`Load ECB, NY Fed and FRED history into the market observations as "${actor.trim()}"?\n\nThis is recorded in the review history, and all dashboard figures will recalculate on the refreshed data.`)) return;
    setBusy(true); setRefreshErr(""); setReport(null);
    try { setReport(await api.engineRefreshMarket(actor.trim())); await load(); onRefreshed(); }
    catch (e: any) { setRefreshErr(e.message); }
    finally { setBusy(false); }
  };
  return (
    <section className="card wide-card" aria-labelledby="ms">
      <div className="hd"><div><h2 id="ms">All market series</h2>
        <p className="sub">Market observations{d ? ` · as of ${when(d.as_of, false)} · ${d.available}/${d.total} series available` : ""} · weekly change and volatility regime</p></div>
        <div className="refresh-box">
          <input className="inp" placeholder="Your name (recorded in the audit log)" value={actor} onChange={e => setActor(e.target.value)} aria-label="Your name for the market data refresh" maxLength={120} disabled={busy} />
          <button className="primary" onClick={refresh} disabled={busy || !actor.trim()} title="Explicit action: Refresh public market observations from ECB, NY Fed and FRED">{busy ? "Loading ECB, NY Fed, FRED…" : "Refresh market data"}</button>
          {!busy && !actor.trim() && <span className="small muted">Enter your name to enable</span>}
        </div></div>
      {refreshErr && <p className="err small" role="alert">Refresh failed: {refreshErr}. The store is unchanged unless the report says otherwise.</p>}
      {report && <RefreshReport r={report} onClose={() => setReport(null)} />}
      {err && <p className="err small">{err}</p>}
      {!d && !err && <p className="muted small">Loading…</p>}
      {d && groupSeries(d.series).map(g => <div key={g.id} className="mgroup">
        <h3 className="mgroup-h">{g.id}{(g.id === "Freight" || g.id === "Fuel and oil") && <span className="small muted"> · WCI and fuel prices are not fetched live; they come from the weekly market update</span>}</h3>
        <div className="mcards">{g.items.map((s: any) => <div key={s.series_id} className={`mcard ${s.stale ? "is-stale" : ""}`}>
          <div className="mc-top"><span className="mc-name" title={SERIES_NAME[s.series_id]}>{SERIES_NAME[s.series_id] ?? s.series_id}</span>
            {s.provenance && <span className={`chip prov-${String(s.provenance).toLowerCase()}`} title={`Provenance${s.confirmed === false ? " · unconfirmed" : ""}`}>{s.provenance}</span>}</div>
          <div className="mc-val">{seriesValue(s)} <small>{s.unit}</small></div>
          <div className="mc-row"><span className={`mc-chg ${(s.weekly_change ?? 0) > 0 ? "up" : (s.weekly_change ?? 0) < 0 ? "down" : ""}`} title={s.previous ? `vs ${s.previous.value} on ${s.previous.date}` : "No comparison print"}>1w {seriesChange(s)}</span>
            {s.volatility_regime ? <span className={`chip vol-${s.volatility_regime}`} title={`Short/long volatility ratio ${s.volatility_ratio}. ${d.regime_rule}`}>{s.volatility_regime}</span>
              : <span className="chip" title={s.volatility_note ?? ""}>regime n/a</span>}
            {s.status !== "OK" && <span className="chip warn">{s.status}{s.days_since_print != null ? ` · ${s.days_since_print}d` : ""}</span>}</div>
          <div className="mc-src small muted">{s.date ?? "no data"} · {String(s.source ?? "—").replace("hand-collected CSV", "Weekly market update").replace("Markets API", "Markets")}{notFetchedLive(s.series_id) ? " · not fetched live" : ""}</div>
          {s.licence && !s.licence.startsWith("public") && <div className="mc-licence">{s.licence.replace(/\s*\(Wording pending decision[^)]*\)/g, "")}</div>}
        </div>)}</div></div>)}
      <p className="foot">The six tiles in Market Metrics are unchanged. ECB, NY Fed and FRED series are public reference data; they are reloaded only by "Refresh market data", which is recorded in the review history.</p>
    </section>
  );
}

function RefreshReport({ r, onClose }: { r: any; onClose: () => void }) {
  const rejected = r.series.filter((s: any) => s.rejected.length);
  return (
    <div className="refresh-report" role="status">
      <div className="hd"><b>Market data load report · {when(r.as_of, false)} (history from {r.start})</b><button className="link small" onClick={onClose}>Close</button></div>
      <p className="small">Loaded {r.loaded} of {r.series.length} market series · {r.rejected_values} rejected values · {r.stale_series.length} stale{r.failed_series.length ? ` · ${r.failed_series.length} not loaded` : ""} · refreshed by {r.actor}</p>
      {r.stale_series.length > 0 && <p className="small">Stale: {r.stale_series.join(", ")}</p>}
      {r.failed_series.length > 0 && <p className="small err">Not loaded: {r.failed_series.join(", ")}</p>}
      {rejected.length > 0 && <details><summary className="small">Rejected values ({r.rejected_values})</summary><ul className="small">{rejected.flatMap((s: any) => s.rejected.map((x: any, i: number) =>
        <li key={s.series_id + i}>{s.series_id} {x.date}: {String(x.value)} ({x.reasons.join(", ")})</li>))}</ul></details>}
      <details><summary className="small">Per series</summary><div className="cash-detail-table"><table><thead><tr><th>Series</th><th>Source / path</th><th>Prints</th><th>Filled</th><th>Last print</th><th>Stale</th><th>Errors</th></tr></thead>
        <tbody>{r.series.map((s: any) => <tr key={s.series_id}><td>{s.series_id}</td><td>{String(s.source ?? "—").replace("hand-collected CSV", "Weekly market update").replace("Markets API", "Markets")}<small>{s.path}</small></td><td>{s.prints}</td><td>{s.filled}</td><td>{s.last_print ?? "—"}</td><td>{s.stale ? "yes" : "no"}</td><td>{s.errors.join("; ")}</td></tr>)}</tbody></table></div></details>
      <p className="small muted">{r.not_fetched_note}</p>
    </div>
  );
}

// ---------------------------------------------------------------- 2. cash flows by driver and month, liquidity paths

const VIEWS = [["baseline_usd_m", "Baseline"], ["scenario_usd_m", "Scenario"], ["delta_usd_m", "Difference"]] as const;

export function CashFlows({ runId, rate, horizonEnd, scenarioName }: { runId?: string; rate: number; horizonEnd?: string; scenarioName?: string }) {
  const [d, setD] = useState<any>(null), [err, setErr] = useState("");
  const [view, setView] = useState<string>("delta_usd_m");
  useEffect(() => {
    if (!runId) return;
    let live = true; setErr("");
    api.engineCashflows(runId).then(r => { if (live) setD(r); }).catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [runId]);
  if (!runId) return null;
  const p = d ? pivotCashflows(d.by_driver_month, view, rate, horizonEnd) : null;
  const liq = d?.liquidity;
  const end = horizonEnd ?? "9999";
  const conv = (pts: any[]) => (pts ?? []).filter(x => x.date <= end).map(x => ({ date: x.date, value: x.liquidity_usd_m / rate }));
  const fmt = (v: number | undefined) => v == null ? "" : num(v, 1);
  return (
    <section className="card wide-card" aria-labelledby="cfv">
      <div className="hd"><div><h2 id="cfv">Cash flows: baseline and scenario</h2>
        <p className="sub">By driver and month{scenarioName ? ` · ${scenarioName}` : ""} · EUR million</p></div>
        <div className="presets" role="tablist" aria-label="Cash flow view" style={{ flex: "none" }}>{VIEWS.map(([k, label]) =>
          <button key={k} role="tab" aria-selected={view === k} className={`preset ${view === k ? "on" : ""}`} onClick={() => setView(k)}>{label}</button>)}</div></div>
      {err && <p className="err small">{err}</p>}
      {!d && !err && <p className="muted small">Loading…</p>}
      {p && <div className="cash-detail-table cf-table"><table>
        <thead><tr><th>Driver</th>{p.months.map(m => <th key={m} className="r">{m}</th>)}<th className="r">Total</th></tr></thead>
        <tbody>{p.drivers.map(dr => <tr key={dr}><td>{DRIVER[dr] ?? dr}</td>{p.months.map(m => { const v = p.cells[dr]?.[m];
          return <td key={m} className={`r ${view === "delta_usd_m" && v ? (v < 0 ? "neg" : "pos") : ""}`}>{fmt(v)}</td>; })}
          <td className={`r b ${view === "delta_usd_m" ? (p.driverTotals[dr] < 0 ? "neg" : "pos") : ""}`}>{fmt(p.driverTotals[dr])}</td></tr>)}</tbody>
        <tfoot><tr><td>Total</td>{p.months.map(m => <td key={m} className="r">{fmt(p.monthTotals[m])}</td>)}<td className="r b">{fmt(p.total)}</td></tr></tfoot>
      </table></div>}
      {p && view === "baseline_usd_m" && <p className="small muted cf-note">Module cash flows from activity after the analysis date only (e.g. freight collected after the collection lag); collections and payments from earlier activity sit in the operating cash-flow run-rate and are not shown.</p>}
      {p && p.drivers.length === 0 && <p className="muted small">No cash lines in this horizon.</p>}
      {liq && <>
        <h3 className="mgroup-h">Liquidity paths <span className="legend-inline"><i className="lg-base" />Baseline <i className="lg-scen" />Scenario <i className="lg-min" />Minimum reserve</span></h3>
        <PathChart baseline={conv(liq.baseline_path)} scenario={conv(liq.scenario_path)} reserve={liq.reserve_usd_m / rate} unit="EUR m" />
        <p className="foot">Liquidity = cash + undrawn credit lines (+ special fund when counted); the gap to the dashed line is headroom. Reserve as used in this run, including any per-run override.</p>
      </>}
      <p className="foot">Displayed in EUR at the analysis-date exchange rate (USD {rate} per EUR); original amounts are in USD. Baseline and scenario use the same scheduled cash flows; amounts already inside the operating cash-flow run-rate move the path only by their difference.</p>
    </section>
  );
}

// ---------------------------------------------------------------- 3. per-run assumption overrides

export function Assumptions({ overrides, onChange, onReset, busy }: { overrides: Record<string, number | boolean | string>; onChange: (key: string, v: number | boolean | string | null) => void; onReset: () => void; busy: boolean }) {
  const [d, setD] = useState<any>(null), [err, setErr] = useState(""), [open, setOpen] = useState(false);
  useEffect(() => { api.engineAssumptions().then(setD).catch(e => setErr(e.message)); }, []);
  const changed = d ? changedOverrides(d.parameters, overrides) : [];
  return (
    <section className={`card assumptions ${changed.length ? "custom" : ""}`} aria-labelledby="as">
      <div className="wi-head">
        <button className="wi-toggle" onClick={() => setOpen(!open)} aria-expanded={open}><h2 id="as">Assumptions</h2><span className="chev2">{open ? "▴" : "▾"}</span></button>
        <span className="chip warn run-only">For this run only · the engine profile is not changed</span>
        <span className="small muted">{d ? `${d.count} adjustable assumptions` : ""}</span>
        <div className="wi-status">{busy && changed.length > 0 && <span className="muted small">Recalculating…</span>}
          {changed.length > 0 && <button className="link small" onClick={onReset}>Reset assumptions</button>}</div>
      </div>
      {changed.length > 0 && <div className="deltas">{changed.map(c => <span key={c.key} title={c.key}>{assumptionName(c.key)} <b>{assumptionText(c.from, c.unit, c.key)} → {assumptionText(c.to, c.unit, c.key)}</b></span>)}
        <span className="small muted">Sent as per-run overrides with the what-if run; the cards above show the result.</span></div>}
      {err && <p className="err small">{err}</p>}
      {open && d && groupAssumptions(d.parameters).map(g => <div key={g.id} className="mgroup">
        <h3 className="mgroup-h">{g.id[0].toUpperCase() + g.id.slice(1)}</h3>
        <div className="asm-grid">{g.items.map((p: any) => {
          const set = p.key in overrides, v = set ? overrides[p.key] : p.value, spec = sliderSpec(p);
          return <div key={p.key} className={`slider asm ${set && v !== p.value ? "set" : ""}`}>
            <div className="sl-top"><label htmlFor={`as-${p.key}`} title={p.key}>{assumptionName(p.key)}</label>
              <span className="sl-val">{assumptionText(v, p.unit, p.key)}</span></div>
            <div className="asm-key" title="Engine key">{p.key}</div>
            {spec ? <div className="sl-track"><input id={`as-${p.key}`} type="range" min={spec.min} max={spec.max} step={spec.step} value={v as number}
                onChange={e => onChange(p.key, Number(e.target.value))} aria-valuetext={assumptionText(v, p.unit, p.key)} /></div>
              : typeof p.value === "boolean" ? <label className="asm-toggle"><input id={`as-${p.key}`} type="checkbox" checked={v as boolean} onChange={e => onChange(p.key, e.target.checked)} /> {v ? "On" : "Off"}</label>
              : Array.isArray(p.allowed_values) ? <select id={`as-${p.key}`} value={String(v)} onChange={e => onChange(p.key, e.target.value)}>{p.allowed_values.map((o: any) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}</select>
              : <span className="small muted">not adjustable here</span>}
            <div className="sl-foot">{spec ? <span>{assumptionText(spec.min, p.unit, p.key)}</span> : <span />}
              {set && v !== p.value ? <button className="link" onClick={() => onChange(p.key, null)}>profile {assumptionText(p.value, p.unit, p.key)}</button> : <span className="chip">{p.label}</span>}
              {spec ? <span>{assumptionText(spec.max, p.unit, p.key)}</span> : <span />}</div>
            {p.rationale && <p className="asm-why small muted">{p.rationale}</p>}
          </div>;
        })}</div></div>)}
      {open && <p className="foot">Shares are shown as percentages and money in millions; overrides are sent in the engine's own units (fractions for shares, bp for spreads). Ranges are the engine's allowed ranges. Overrides apply to this what-if run only; they do not reach the Assistant drawer.</p>}
    </section>
  );
}

// ---------------------------------------------------------------- hypothetical bunker fuel hedge (read-only)

const eurM = (usd: number | null | undefined, rate: number, d = 1) => usd == null ? "—" : `${usd < 0 ? "−" : usd > 0 ? "+" : ""}€${Math.abs(usd / rate).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}m`;
const lvl = (usd: number | null | undefined, rate: number) => usd == null ? "—" : num(usd / rate, 0);

export function HedgeTool({ request, requestKey, rate, asOf }: { request: Record<string, unknown>; requestKey: string; rate: number; asOf: string }) {
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<any>(null), [err, setErr] = useState("");
  const [target, setTarget] = useState("fuel_all"), [instrument, setInstrument] = useState("swap");
  const [coverage, setCoverage] = useState(50), [tenor, setTenor] = useState(12), [start, setStart] = useState("");
  const [busy, setBusy] = useState(false), [res, setRes] = useState<any>(null), [ranFor, setRanFor] = useState("");
  useEffect(() => { if (open && !opts) api.engineHedgeOptions().then(setOpts).catch(e => setErr(e.message)); }, [open, opts]);
  const t0 = opts?.as_of ?? asOf.slice(0, 10);
  const run = async () => {
    setBusy(true); setErr("");
    try {
      const hedge = { target, instrument, coverage_pct: clampTo(coverage, opts?.coverage_pct_range), tenor_months: clampTo(tenor, opts?.tenor_months_range, true),
                      start_date: start || t0 };
      setRes(await api.engineHedgeTest({ ...request, hedge })); setRanFor(requestKey);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const months = res ? hedgeMonths(res.settlement_by_month, res.margin_by_month, rate) : [];
  const mt = res?.margin_test;
  const raised: any[] = res?.desk_exceptions_raised_by_hedge ?? [];
  return (
    <section className="card wide-card hedge" aria-labelledby="ht">
      <div className="wi-head">
        <button className="wi-toggle" onClick={() => setOpen(!open)} aria-expanded={open}><h2 id="ht">Hedge tool · bunker fuel</h2><span className="chev2">{open ? "▴" : "▾"}</span></button>
        <span className="chip warn run-only">Hypothetical hedge test · nothing is executed</span>
        <span className="small muted">Runs against the currently selected scenario, with and without the hedge, and against its mirror</span>
      </div>
      {open && <>
        <div className="hedge-inputs">
          <label>Target<select value={target} onChange={e => setTarget(e.target.value)}>{(opts?.targets ?? [{ id: "fuel_all", label: "All fuel" }]).map((t: any) =>
            <option key={t.id} value={t.id}>{t.id === "fuel_all" ? "All fuel" : t.id.toUpperCase()} · {t.label}</option>)}</select></label>
          <label>Instrument<select value={instrument} onChange={e => setInstrument(e.target.value)}>{(opts?.instruments ?? [{ id: "swap", label: "Swap" }]).map((i: any) =>
            <option key={i.id} value={i.id}>{i.label}</option>)}</select></label>
          <label>Coverage <b>{coverage}%</b><input type="range" min={opts?.coverage_pct_range?.[0] ?? 0} max={opts?.coverage_pct_range?.[1] ?? 100} step={5} value={coverage} onChange={e => setCoverage(Number(e.target.value))} /></label>
          <label>Tenor <b>{tenor} months</b><input type="range" min={opts?.tenor_months_range?.[0] ?? 1} max={opts?.tenor_months_range?.[1] ?? 36} step={1} value={tenor} onChange={e => setTenor(Number(e.target.value))} /></label>
          <label>Start date<input type="date" className="inp" value={start || t0} min={t0} onChange={e => setStart(e.target.value)} /></label>
          <button className="primary" onClick={run} disabled={busy}>{busy ? "Running 4 engine runs…" : "Test hedge on current scenario"}</button>
        </div>
        {opts && <p className="small muted">Strike = target price at T0 ({t0}). Ranges are the engine's: coverage {opts.coverage_pct_range.join("–")}%, tenor {opts.tenor_months_range.join("–")} months.</p>}
        {err && <p className="err small" role="alert">{err}</p>}
        {res && <div className={`hedge-result ${ranFor !== requestKey ? "stale-result" : ""}`}>
          <div className="hd"><b>Hypothetical hedge test · nothing is executed</b>
            <span className="small muted">{res.hedge.coverage_pct}% {res.hedge.target === "fuel_all" ? "all fuel" : res.hedge.target.toUpperCase()} {res.hedge.instrument === "swap" ? "swap" : "fixed-price supply"} · {res.hedge.tenor_months} months from {res.hedge.start_date} · scenario “{res.scenario}”</span></div>
          {ranFor !== requestKey && <p className="small warnline">The scenario has changed since this test; run it again for the current scenario.</p>}
          <div className="x-tiles hedge-tiles">
            <div className="x-tile"><span>Saving · adverse direction (fuel up)</span><b>{eurM(res.saving_adverse_usd_m, rate)}</b><em>Change in minimum headroom under the {res.adverse_is === "scenario" ? "selected" : "mirrored"} scenario</em></div>
            <div className="x-tile"><span>Cost · favourable direction (fuel down)</span><b>{eurM(res.cost_favourable_usd_m, rate)}</b><em>Reduction in minimum headroom under the {res.adverse_is === "scenario" ? "mirrored" : "selected"} scenario</em></div>
            <div className="x-tile"><span>Minimum headroom, this scenario</span><b>{lvl(res.min_headroom_usd_m.without, rate)} → {lvl(res.min_headroom_usd_m.with, rate)}</b><em>{eurM(res.min_headroom_usd_m.change, rate)} with the hedge · EUR m</em></div>
            <div className={`x-tile ${mt.passed ? "" : "bad"}`}><span>Margin-eligible test</span><b>{!mt.applicable ? "n/a · no margin" : mt.passed ? "Pass" : "Shortfall"}</b>
              <em>{mt.applicable ? <>Lowest margin-eligible balance <span data-tip={eurWithUsd(mt.lowest_margin_eligible_balance_usd_m, rate).tip}>{eurWithUsd(mt.lowest_margin_eligible_balance_usd_m, rate).text}</span>{mt.shortfall_date ? ` · short from ${when(mt.shortfall_date, false)}` : mt.lowest_date ? ` on ${when(mt.lowest_date, false)}` : ""}</> : "Fixed-price supply has no margin calls"}</em></div>
            <div className="x-tile"><span>Status without → with</span><b><span className={`chip st-${res.status.without.toLowerCase()}`}>{res.status.without}</span> → <span className={`chip st-${res.status.with.toLowerCase()}`}>{res.status.with}</span></b>
              <em>{raised.length ? raised.map((e, i) => { const d = deskExceptionText(e, rate, res.engine_horizon_end);
                return <span key={i} className="desk-raised">{d.label}:{" "}
                  <span data-tip={d.usdTip || undefined}>{d.text}</span>{d.measure && <small className="desk-measure">{d.measure}</small>}</span>; })
                : "No desk exception raised by the hedge"}</em></div>
          </div>
          {res.margin_on_t0_usd_m !== 0 && <p className="small warnline">Margin of {eurM(res.margin_on_t0_usd_m, rate)} is dated on T0 ({res.as_of}). {res.margin_on_t0_note}</p>}
          {months.length > 0 && <details open={months.length <= 14}><summary className="small">Settlement and margin cash by month (EUR m, selected scenario, with the hedge)</summary>
            <div className="cash-detail-table cf-table"><table><thead><tr><th>Month</th><th className="r">Settlement</th><th className="r">Margin</th><th className="r">Total</th></tr></thead>
              <tbody>{months.map(r => <tr key={r.month}><td>{r.month}</td><td className={`r ${r.settlement < 0 ? "neg" : r.settlement > 0 ? "pos" : ""}`}>{num(r.settlement, 1)}</td>
                <td className={`r ${r.margin < 0 ? "neg" : r.margin > 0 ? "pos" : ""}`}>{num(r.margin, 1)}</td><td className="r b">{num(r.settlement + r.margin, 1)}</td></tr>)}</tbody></table></div></details>}
          {res.mirror_notes?.length > 0 && <p className="small muted">Mirror: {res.mirror_notes.join("; ")}</p>}
        </div>}
        <p className="foot">Saving and cost are changes in the minimum headroom, estimated hedge impacts, shown in EUR at the analysis-date exchange rate. A swap settles monthly and posts margin when prices fall below the strike (one-sided collateral). The fixed-price supply contract has no margin calls but carries supplier credit risk. This comparison does not execute a hedge.</p>
      </>}
    </section>
  );
}
