import { AgentBullets } from "../AgentBullets";
import { inputLabel, inputChange } from "./metricAssumptions";
import { treasuryText } from "../treasuryCopy";
// Treasury recommendation agent: one ranked list of review actions and optional engine-tested hedges.
// Every figure is the engine's (USD m, as returned); nothing here can trade or hedge.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { Drawer } from "../Drawer";
import { when } from "../format";
import { agentRequest, defaultWindow, effectRows, gradeOf, hedgeLabel, MODULE, originOf, reliabilityNotes, eurAmount, assessmentIsStale, windowProblem } from "./hedgeAgentView";

function Box({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return <section className={`dx-box ${className}`}><h3>{title}</h3>{children}</section>;
}

type Props = { asOf: string; shocks: Record<string, number>; overrides: Record<string, unknown>; forecastInput?: {scenario: any; forecast_id?: string; as_of?: string}; horizonDays?: number; rate?: number; scenarioName?: string; scenarioKey?: string };

export function HedgeAgentCard({ asOf, shocks, overrides, forecastInput, horizonDays, rate, scenarioName, scenarioKey }: Props) {
  const [win, setWin] = useState(() => defaultWindow(asOf));
  const [withWhatIf, setWithWhatIf] = useState(false);
  const [session, setSession] = useState<any | null>(null);
  const [resultKey, setResultKey] = useState<string | null>(null);
  const [resultScenario, setResultScenario] = useState("");
  const contextKey = JSON.stringify([asOf, scenarioKey, shocks, overrides, forecastInput, horizonDays, win, withWhatIf]);
  const stale = !!session && assessmentIsStale(resultKey, contextKey);
  const money = (value: number | null | undefined, decimals = 1, signed = false) => eurAmount(value, rate, decimals, signed);
  const [history, setHistory] = useState<any[]>([]);
  const [busy, setBusy] = useState(false), [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<any | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const today = new Date().toISOString().slice(0, 10);
  const problem = windowProblem(win, today > asOf ? today : asOf);
  const whatIfSet = Object.keys(shocks).length > 0 || Object.keys(overrides).length > 0 || !!forecastInput;

  const loadHistory = () => api.recommendationSessions().then(d => setHistory(d.sessions ?? [])).catch(() => setHistory([]));
  useEffect(() => { void loadHistory(); }, []);
  useEffect(() => () => window.clearInterval(timer.current), []);

  useEffect(() => { setOpen(null); }, [contextKey]);
  const run = async () => {
    const submittedKey = contextKey;
    const submittedScenario = withWhatIf ? (scenarioName ?? "Manual assumptions") : "News assessment only";
    setBusy(true); setError(null); setElapsed(0);
    const start = Date.now();
    timer.current = window.setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 1000);
    try { setSession(await api.recommendationAgent(agentRequest(win, withWhatIf, shocks, overrides, forecastInput, horizonDays))); setResultKey(submittedKey); setResultScenario(submittedScenario); void loadHistory(); }
    catch (e: any) { setError(e?.message ?? String(e)); }
    finally { window.clearInterval(timer.current); setBusy(false); }
  };
  const pick = async (id: string) => {
    if (!id) return;
    setError(null);
    try { setSession(await api.recommendationSession(id)); setResultKey(null); setResultScenario("Saved historical assessment"); setOpen(null); } catch (e: any) { setError(e?.message ?? String(e)); }
  };

  const d = session?.diagnosis;
  return <section className="card hedge-agent" aria-labelledby="hedge-agent-title">
    <div className="hd"><div><h2 id="hedge-agent-title">Treasury recommendations</h2>
      <p className="sub">Prioritizes cash collection, funding, operational, monitoring and hedge reviews from the selected News</p></div></div>
    <div className="ha-controls">
      <label>News from <input type="text" inputMode="numeric" placeholder="YYYY-MM-DD" value={win.from} onChange={e => setWin({ ...win, from: e.target.value })} /></label>
      <label>to <input type="text" inputMode="numeric" placeholder="YYYY-MM-DD" value={win.to} onChange={e => setWin({ ...win, to: e.target.value })} /></label>
      <label className="ha-check" title={whatIfSet ? "Uses the selected scenario, slider moves and assumption overrides" : "No custom scenario is selected"}>
        <input type="checkbox" checked={withWhatIf && whatIfSet} disabled={!whatIfSet} onChange={e => setWithWhatIf(e.target.checked)} /> Include my What-if moves</label>
      <button className="primary" disabled={busy || !!problem} onClick={() => void run()}>{busy ? "Running…" : "Assess actions"}</button>
      {history.length > 0 && <select aria-label="Previous sessions" value="" onChange={e => void pick(e.target.value)}>
        <option value="">Previous sessions…</option>
        {history.map(h => <option key={h.session_id} value={h.session_id}>{when(h.created_at)} · News {h.news_window?.from ?? "…"}–{h.news_window?.to ?? "…"} · {h.recommendations} rec.</option>)}
      </select>}
    </div>
    <p className="small muted">The analysis date is the end of the window: no News or market data after it is used.</p>
    {problem && <p className="small err">{problem}</p>}
    {busy && <p className="rec-progress" role="status"><span className="spinner" aria-hidden />Assessing responses ({elapsed}s). Risk analysis and hedge comparisons may take several minutes.</p>}
    {error && <p className="small err" role="alert">{error}</p>}

    {session && <>
      {stale && <p className="banner warn" role="status"><b>Previous assessment — not the current dashboard scenario.</b> The scenario, horizon or news selection may differ. Select Assess actions to refresh.</p>}
      <section className="dx-box"><h3>Assessment scope</h3>
        <p>{resultScenario}. {session.forecast?.mode === "manual" ? "Uses the selected standard scenario and included manual inputs. News provides review context; no extra news assumptions are added." : resultScenario === "News assessment only" ? "Uses news-based assumptions only, without your What-if moves. These figures are separate from the main dashboard forecast." : "Uses the news assumptions and manual inputs listed below. These figures are separate from the main dashboard forecast."}</p>
        <p className="small muted">Tables show EUR million using the current dashboard exchange rate{rate ? ` (USD ${rate} per EUR, ${asOf})` : " — unavailable"}. Source amounts quoted in written assessments retain their stated currency.</p>
        <details><summary>Assumptions used in this assessment</summary>
          <ul>{(session.forecast?.predictions ?? []).map((p: any, i: number) => <li key={i}>{inputLabel(p.param_key)}: {inputChange(p)}</li>)}</ul>
          {!(session.forecast?.predictions?.length) && <p>No quantified news assumptions were added.</p>}
          <p>Included manual inputs: {Object.entries(session.page_shocks ?? {}).map(([k,v]) => `${({fx_eur:"EUR move (%)",fx_gbp:"GBP move (%)",fx_cny:"CNY move (%)",fx_cad:"CAD move (%)",interest:"Benchmark change (bp)",bunker:"Fuel price change (USD/t)",freight:"Freight rate change (%)",collection_delay:"Collection delay (days)"} as Record<string,string>)[k] ?? "Scenario assumption"}: ${v}`).join("; ") || "None"}.</p>
        </details>
      </section>
      <div className="ha-meta">
        <span className={`chip ${session.origin === "hybrid" ? "warn" : ""}`} title={originOf(session.origin).help}>{originOf(session.origin).label}</span>
        <span className="small muted">{when(session.created_at)} · News {session.news_window?.from ?? "…"} to {session.news_window?.to ?? "…"} · {session.event_ids?.length ?? 0} Events</span>
      </div>
      {d && <div className="ha-diag">
        <div><span>Lowest headroom (scenario)</span><b>{money(d.scenario_min_headroom_usd_m, 0)}</b><small>{when(d.scenario_min_date, false)} · {d.binding_date_precision === "day" ? "daily" : "to the month"}</small></div>
        <div><span>Headroom consumed</span><b className={d.headroom_consumed_usd_m > 0 ? "neg" : ""}>{money(d.headroom_consumed_usd_m, 0)}</b><small>vs baseline</small></div>
        <div><span>First reserve breach</span><b>{d.first_breach_date ? when(d.first_breach_date, false) : "None"}</b><small>{(d.shortfall_tier ?? "").replace(/_/g, " ")}</small></div>
        <div><span>Hurt by</span><b>{d.adverse_modules?.length ? d.adverse_modules.map((m: string) => MODULE[m] ?? m).join(", ") : "None"}</b><small>cash-flow drivers</small></div>
      </div>}
      <details className="ha-summary"><summary>Treasury assessment</summary><AgentBullets text={session.summary} /></details>
      {session.recommendations.length === 0
        ? <p className="small muted">No action is recommended for this window. {session.candidates?.length ? `${session.candidates.length} tested hedge(s) are listed below.` : ""}</p>
        : <div className="rec-grid">{session.recommendations.map((rec: any) => {
            const g = gradeOf(rec.grade);
            return <button key={rec.recommendation_id} className="action ha-rec" onClick={() => setOpen(rec)}>
              <div><div className="atitle">{rec.rank}. {rec.title} <span className={`chip ${g.cls}`}>{g.label}</span></div>
                <AgentBullets className="rec-takeaways" text={rec.effect ? [`Impact: change in minimum headroom ${money(rec.effect.min_headroom_change_usd_m, 1, true)}.`, `Action to review: ${hedgeLabel(rec)}`] : ["Cash effect is not quantified.", `Reason: ${rec.grade_reason || "This action has no quantified cash estimate."}`, `Confirm: ${rec.conditions?.[0] || "The exposure, amount and timing."}`, `Next: ${rec.title}`]} />
                <div className="small muted">{rec.effect ? <>Change in minimum headroom {money(rec.effect.min_headroom_change_usd_m, 1, true)} · gives up {money(rec.effect.cost_if_reverses_over_horizon_usd_m)} if reversed · cover from {when(rec.timing.cover_needed_from, false)}</> : "Effect not quantified · review conditions before acting"}</div></div>
              <span className="chev" aria-hidden>{"›"}</span></button>;
          })}</div>}
      {session.candidates?.length > 0 && <details className="ha-cands"><summary>All tested hedges ({session.candidates.length})</summary>
        <table className="t dx-table"><thead><tr><th>Hedge</th><th>Grade</th><th className="r">Change in minimum headroom</th><th className="r">Offset</th><th className="r">Given up if reversed</th></tr></thead><tbody>
          {session.candidates.map((c: any) => <tr key={c.candidate_id}><td>{hedgeLabel(c)}</td><td><span className={`chip ${gradeOf(c.grade).cls}`}>{gradeOf(c.grade).label}</span>{c.effect.over_hedge ? <span className="chip bad">over-hedge</span> : null}</td>
            <td className="r">{money(c.effect.min_headroom_change_usd_m, 1, true)}</td><td className="r">{c.effect.offset_of_driver_loss_pct == null ? "—" : `${Math.round(c.effect.offset_of_driver_loss_pct)}%`}</td>
            <td className="r">{money(c.effect.cost_if_reverses_over_horizon_usd_m)}</td></tr>)}
        </tbody></table></details>}
    </>}
    <p className="foot">Review proposals only. Tested hedges have estimated cash impacts; other actions have no quantified effect. Nothing is executed.</p>
    {open && session && <HedgeRecDetail session={session} rec={open} rate={rate} onClose={() => setOpen(null)} />}
  </section>;
}

export function HedgeRecDetail({ session, rec, rate, onClose }: { session: any; rec: any; rate?: number; onClose: () => void }) {
  const money = (value: number | null | undefined, decimals = 1, signed = false) => eurAmount(value, rate, decimals, signed);
  if (rec.kind && rec.kind !== "hedge") return <Drawer title={rec.title} modal wide onClose={onClose}>
    <div className="dx">
      <p className="sub">{rec.kind.replace(/_/g, " ")} · Effect not quantified · For Treasury review</p>
      <Box title="Proposed response"><AgentBullets text={[rec.rationale, `Timing: ${rec.why_now}`]} /></Box>
      <Box title="Verify before acting"><ul>{rec.conditions.map((x: string) => <li key={x}>{treasuryText(x)}</li>)}</ul></Box>
      <Box title="Alternatives"><ul>{rec.alternatives.map((x: string) => <li key={x}>{treasuryText(x)}</li>)}</ul></Box>
      <Box title="News evidence">
        {(session.events ?? []).filter((e: any) => rec.event_ids.includes(e.event_id)).map((e: any) =>
          <div key={e.event_id}><b>{e.title}</b><ul>{(e.articles ?? []).map((a: any) =>
            <li key={a.article_id}><a href={a.url} target="_blank" rel="noreferrer noopener">{a.title}</a></li>)}</ul></div>)}
      </Box>
      <Box title="Limits"><AgentBullets text={rec.grade_reason} /><ul>{reliabilityNotes(session).map(x => <li key={x}>{treasuryText(x)}</li>)}</ul></Box>
    </div>
  </Drawer>;
  const g = gradeOf(rec.grade), o = originOf(rec.origin), t = rec.timing, d = session.diagnosis ?? {};
  const shocks = Object.entries(session.page_shocks ?? {});
  const predictions: any[] = session.forecast?.predictions ?? [];
  const limits: string[] = rec.record?.risks_and_limits?.not_covered ?? [];
  const months = (rows: any[]) => rows?.length ? <table className="t dx-table"><tbody>{rows.map((x: any) => <tr key={x.month}><th>{x.month}</th><td className={`r ${x.usd_m < 0 ? "neg" : "pos"}`}>{money(x.usd_m, 1, true)}</td></tr>)}</tbody></table> : <p className="small muted">None in the horizon.</p>;
  return <Drawer title={rec.title} modal wide onClose={onClose}>
    <div className="dx">
      <header className="dx-top">
        <div><p className="sub">{MODULE[rec.module] ?? rec.module} · {hedgeLabel(rec)}</p>
          <p className="a-areas small"><span className={`chip ${g.cls}`} title={g.help}>{g.label}</span><span className="chip" title={o.help}>{o.label}</span><span className="chip warn">For Treasury review</span></p>
          <p className="small muted">Generated {when(session.created_at)}</p></div>
        <div><span>Change in minimum headroom</span><b className={`dx-big ${rec.effect.min_headroom_change_usd_m > 0 ? "pos" : ""}`}>{money(rec.effect.min_headroom_change_usd_m, 1, true)}</b></div>
        <div><span>Given up if the scenario reverses</span><b className="neg">{money(rec.effect.cost_if_reverses_over_horizon_usd_m)}</b><small> over the horizon</small></div>
        <div><span>Cover needed from</span><b>{when(t.cover_needed_from, false)}</b><small> binding {when(t.binding_date_without, false)}</small></div>
      </header>
      <div className="dx-cols">
        <div>
          <Box title="What triggered this">
            {session.events?.length ? <ul>{session.events.map((e: any) => <li key={e.event_id}><b>{e.title}</b> <span className="small muted">· {e.category} · published {when(e.latest_published_at, false)}{e.score?.total != null ? ` · priority ${e.score.total}/100` : ""}</span>
              <div className="small">{e.articles?.map((a: any) => <a key={a.article_id} href={a.url} target="_blank" rel="noreferrer noopener">{a.publisher ?? "Source"}: {a.title}</a>).reduce((acc: any[], x: any, i: number) => i ? [...acc, " · ", x] : [x], [])}</div></li>)}</ul>
              : <p className="small muted">No News Event is linked; the trigger is the scenario's cash impact.</p>}
            {predictions.length > 0 && <p className="small"><b>News scenario inputs (ASSUMPTION):</b> {predictions.map(p => `${inputLabel(p.param_key)} ${inputChange(p)}`).join("; ")}</p>}
            {shocks.length > 0 && <p className="small"><b>Your What-if moves (user assumption, not News):</b> {shocks.map(([k, v]) => `${k} ${Number(v) > 0 ? "+" : ""}${v}`).join("; ")}</p>}
            <p className="small">Liquidity assessment: lowest scenario headroom {money(d.scenario_min_headroom_usd_m, 0)} on {when(d.scenario_min_date, false)}; the scenario consumes {money(d.headroom_consumed_usd_m, 0)}; first breach {d.first_breach_date ? when(d.first_breach_date, false) : "none"}.</p>
          </Box>
          <Box title="Cash impact (EUR million)">
            <table className="t dx-table"><tbody>{effectRows(rec, money).map(([k, v, tone]) => <tr key={k}><th>{k}</th><td className={`r ${tone}`}>{v}</td></tr>)}</tbody></table>
            <p className="small muted">Selected scenario and its mirror (every shock reversed), each run with and without the hedge.</p>
          </Box>
          <Box title="Timing">
            <table className="t dx-table"><tbody>
              <tr><th>Hedge start tested</th><td>{when(t.start_date, false)}</td></tr>
              <tr><th>First adverse {MODULE[rec.module] ?? rec.module} cash</th><td>{when(t.cover_needed_from, false)}{t.starts_after_first_adverse_cash ? " · the start is later, earlier flows stay unhedged" : ""}</td></tr>
              <tr><th>Binding date (lowest headroom)</th><td>{when(t.binding_date_without, false)} → {when(t.binding_date_with, false)} with the hedge · {t.binding_date_precision}</td></tr>
              <tr><th>First reserve breach</th><td>{t.first_breach_without ? when(t.first_breach_without, false) : "None"} → {t.first_breach_with ? when(t.first_breach_with, false) : "None"}</td></tr>
            </tbody></table>
            {rec.settlement_by_month?.length > 0 && <><p className="small"><b>Hedge settlement by month</b></p>{months(rec.settlement_by_month)}</>}
            {rec.margin_test?.applicable && <><p className="small"><b>Margin calls by month</b></p>{months(rec.margin_by_month)}</>}
          </Box>
        </div>
        <div>
          <Box title="Why this hedge">
            <AgentBullets text={rec.rationale} />
            <AgentBullets text={`Timing: ${rec.why_now}`} />
            {rec.alternatives?.length > 0 && <><p><b>Alternatives</b></p><ul>{rec.alternatives.map((x: string) => <li key={x}>{treasuryText(x)}</li>)}</ul></>}
            <p className="small muted">{o.help}</p>
          </Box>
          <Box title="Assumptions and limitations" className="dx-sources">
            <AgentBullets text={[`Effect: ${g.label}.`, rec.grade_reason, "Thresholds are illustrative, not Treasury policy."]} />
            <ul>{[...limits, ...reliabilityNotes(session)].map(x => <li key={x}>{treasuryText(x)}</li>)}</ul>
          </Box>
          <Box title="Verify before acting">
            <ul>{(rec.conditions ?? []).map((x: string) => <li key={x}>{treasuryText(x)}</li>)}</ul>
            <p className="small"><b>Human approval is required</b> before any hedge, trade or payment. This panel records nothing and executes nothing.</p>
          </Box>

        </div>
      </div>
    </div>
    <footer className="dx-foot"><span className="small muted">{session.disclaimer}</span><button className="primary" onClick={onClose}>Close</button></footer>
  </Drawer>;
}
