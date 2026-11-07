import { AgentBullets } from "./AgentBullets";
// Reference & admin tools from the uploaded frontend that run on the current backend: driver diagnostic
// (POST /api/v1/risk/run with an engine preset), the risk assistant with News analysis, and the assumption
// adjustment review queue. Each tool is self-contained; none of them changes the dashboard's selected scenario.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { RunStatus } from "./EnginePanels";


const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const titleCase = (value: string) => value.replace(/[._-]+/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
const valueText = (value: unknown) => typeof value === "boolean" ? (value ? "On" : "Off") : value == null ? "Not recorded" : String(value);
const usdMillions = (value: unknown) => {
  const amount = Number(value);
  return Number.isFinite(amount) ? `${amount < 0 ? "−" : amount > 0 ? "+" : ""}$${Math.abs(amount).toLocaleString("en-US", { maximumFractionDigits: 1 })}m` : "Not available";
};

export function RiskDiagnostic({ asOf, eurRate }: { asOf: string; eurRate: number | null }) {
  const [presets, setPresets] = useState<any[]>([]), [selectedId, setSelectedId] = useState("");
  const [runDate, setRunDate] = useState(asOf), [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => { setRunDate(asOf); }, [asOf]);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await api.riskPresets();
      const items = Array.isArray(response?.presets) ? response.presets : [];
      setPresets(items);
      setSelectedId(current => current || items.find((item: any) => item.id !== "baseline")?.id || items[0]?.id || "");
    } catch (error) { setError(errorText(error, "Risk diagnostics could not be loaded.")); }
    finally { setLoading(false); }
  }, [asOf]);
  useEffect(() => { void load(); }, [load]);
  const selected = useMemo(() => presets.find(item => item.id === selectedId), [presets, selectedId]);
  const run = async () => {
    if (!selected || !runDate) return;
    setBusy(true); setError(""); setResult(null);
    try { setResult(await api.riskRun(runDate, { ...selected.scenario, start_date: runDate }, false)); }
    catch (error) { setError(errorText(error, "The diagnostic could not be calculated.")); }
    finally { setBusy(false); }
  };
  const attribution = result ? Object.entries(result.attribution ?? {}).filter(([key, value]) => key !== "total" && Math.abs(Number(value)) > 0.0001) : [];
  return <section className="advanced-tool-panel" aria-labelledby="advanced-scenario-heading">
    <h3 id="advanced-scenario-heading">Driver scenario and sensitivity</h3>
    <p className="small muted">Standalone run of one engine preset. It does not change the scenario selected on the dashboard.</p>
    {loading && <p className="small muted" role="status">Loading diagnostic scenarios…</p>}
    {error && <p className="small err" role="alert">{error} <button className="link" onClick={() => void load()}>Retry</button></p>}
    {!loading && <div className="advanced-controls">
      <label>Diagnostic scenario<select value={selectedId} onChange={event => { setSelectedId(event.target.value); setResult(null); }}>
        {presets.map(item => <option key={item.id} value={item.id}>{item.name} · {titleCase(item.group ?? "diagnostic")}</option>)}
      </select></label>
      <label>As-of date<input className="inp" type="date" value={runDate} onChange={event => { setRunDate(event.target.value); setResult(null); }} /></label>
      <button className="primary" disabled={busy || !selectedId || !runDate} onClick={() => void run()}>{busy ? "Calculating…" : "Run diagnostic"}</button>
    </div>}
    {selected?.description && <p className="advanced-description">{selected.description}</p>}
    {result && <div className="advanced-result" role="status">
      <div className="advanced-result-metrics">
        <span>Status <b>{result.status ?? "Not available"}</b></span>
        <span>Minimum headroom <b>{usdMillions(result.headroom?.scenario_min_usd_m)}</b></span>
        <span>Net scenario cash effect <b>{usdMillions(result.attribution?.total)}</b></span>
      </div>
      {result.headroom?.scenario_min_date && <p className="small muted">Lowest headroom date: {result.headroom.scenario_min_date}</p>}
      {attribution.length > 0 && <details><summary>Driver contribution</summary><ul>{attribution.map(([key, value]) => <li key={key}>{titleCase(key)}: {usdMillions(value)}</li>)}</ul></details>}
      {eurRate && <RunStatus calc={result} rate={eurRate} />}
      {(result.missing_inputs?.length > 0 || result.limitations?.length > 0) && <details><summary>Coverage and limitations</summary>
        {result.missing_inputs?.length > 0 && <p>Missing inputs: {result.missing_inputs.map(titleCase).join(", ")}</p>}
        {result.limitations?.length > 0 && <ul>{result.limitations.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>}
      </details>}
    </div>}
  </section>;
}

export function AssistantAndNews({ asOf }: { asOf: string }) {
  const [question, setQuestion] = useState(""), [answer, setAnswer] = useState<any>(null), [brief, setBrief] = useState<any>(null);
  const [assistantBusy, setAssistantBusy] = useState(false), [briefBusy, setBriefBusy] = useState(false), [error, setError] = useState("");
  const ask = async (mode: "question" | "briefing") => {
    if (mode === "question" && !question.trim()) return;
    setAssistantBusy(true); setError("");
    try {
      setAnswer(await api.assistant(question.trim(), mode, 12, {}, { as_of: asOf, scenario: { name: "Risk assistant diagnostic", start_date: asOf } }));
    } catch (error) { setError(errorText(error, "The risk assistant is unavailable.")); }
    finally { setAssistantBusy(false); }
  };
  const loadBrief = async () => {
    setBriefBusy(true); setError("");
    try { setBrief(await api.brief()); }
    catch (error) { setError(errorText(error, "News analysis is unavailable.")); }
    finally { setBriefBusy(false); }
  };
  return <section className="advanced-tool-panel" aria-labelledby="advanced-assistant-heading">
    <h3 id="advanced-assistant-heading">Risk assistant</h3>
    <p className="small muted">Answers use the dashboard figures for the baseline over the next 12 months, not the scenario selected above; use "Ask" for questions about the selected scenario.</p>
    <label className="advanced-question">Treasury question<textarea value={question} maxLength={2000} rows={3} onChange={event => setQuestion(event.target.value)} placeholder="What is the largest market-risk exposure?" /></label>
    <div className="advanced-button-row"><button className="primary" disabled={assistantBusy || !question.trim()} onClick={() => void ask("question")}>{assistantBusy ? "Working…" : "Ask assistant"}</button><button className="link" disabled={assistantBusy} onClick={() => void ask("briefing")}>Create briefing</button></div>
    {answer && <div className="advanced-answer" role="status"><AgentBullets text={answer.answer} /></div>}
    <hr />
    <h4>News analysis <span className="chip warn">Not a weekly Treasury brief</span></h4>
    <p className="small muted">Cumulative saved News Event analysis. It is not a governed weekly Treasury report.</p>
    <button className="link" disabled={briefBusy} onClick={() => void loadBrief()}>{briefBusy ? "Loading News analysis…" : "Load News analysis"}</button>
    {brief && <div className="advanced-answer" role="status"><p><b>{brief.status ? titleCase(String(brief.status)) : "News status"}</b></p><AgentBullets text={brief.executive_summary || brief.message} />{brief.message && brief.executive_summary && <AgentBullets className="small muted" text={brief.message} />}</div>}
    {error && <p className="small err" role="alert">{error}</p>}
  </section>;
}

export function AdjustmentQueue() {
  const [items, setItems] = useState<any[]>([]), [actor, setActor] = useState(""), [busyId, setBusyId] = useState("");
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [message, setMessage] = useState("");
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const response = await api.intelligenceAdjustments(); setItems(response?.adjustments ?? []); }
    catch (error) { setError(errorText(error, "The adjustment review queue could not be loaded.")); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const decide = async (item: any, decision: "approved" | "rejected" | "revoked") => {
    if (!actor.trim()) return;
    const verb = decision === "approved" ? "Approve" : decision === "rejected" ? "Reject" : "Revoke";
    if (!window.confirm(`${verb} this Treasury assumption adjustment as ${actor.trim()}? The decision is recorded in the audit trail.`)) return;
    const id = item.adjustment_id ?? item.id;
    setBusyId(id); setError(""); setMessage("");
    try { await api.adjustmentDecision(id, decision, actor.trim()); setMessage(`${verb} decision recorded.`); await load(); }
    catch (error) { setError(errorText(error, "The review decision could not be recorded.")); }
    finally { setBusyId(""); }
  };
  return <section className="advanced-tool-panel" aria-labelledby="advanced-adjustment-heading">
    <h3 id="advanced-adjustment-heading">Assumption adjustment review</h3>
    <p className="small muted">Proposals remain inactive until a named person approves them. Approved adjustments affect eligible future deterministic runs; saved runs never change.</p>
    <div className="advanced-controls"><label>Your name<input className="inp" value={actor} maxLength={120} onChange={event => setActor(event.target.value)} /></label><button className="link" disabled={loading} onClick={() => void load()}>Refresh queue</button></div>
    {loading && <p className="small muted" role="status">Loading adjustment queue…</p>}
    {error && <p className="small err" role="alert">{error}</p>}
    {message && <p className="small advanced-success" role="status">{message}</p>}
    {!loading && !items.length && <p className="advanced-empty">No adjustment proposals are saved.</p>}
    <div className="advanced-list">{items.map(item => {
      const id = item.adjustment_id ?? item.id, pending = item.status === "proposed";
      return <article className="advanced-list-item" key={id}>
        <div className="advanced-item-heading"><b>{titleCase(item.param_key ?? "Treasury assumption")}</b><span className={`chip ${pending ? "warn" : item.status === "approved" ? "ok" : ""}`}>{titleCase(item.status ?? "unknown")}</span></div>
        <p>{valueText(item.old_value)} → <b>{valueText(item.new_value)}</b>{item.step ? ` · ${titleCase(item.step)} step` : ""}</p>
        <p className="small muted">Effective {item.effective_date} · review by {item.review_date}</p>
        {item.justification && <AgentBullets className="small" text={item.justification} />}
        {item.review_flag && <p className="small err">Source Event changed after approval; review is required.</p>}
        {pending && <div className="advanced-button-row"><button className="primary" disabled={!actor.trim() || busyId === id} onClick={() => void decide(item, "approved")}>Approve</button><button className="link" disabled={!actor.trim() || busyId === id} onClick={() => void decide(item, "rejected")}>Reject</button></div>}
        {item.status === "approved" && <button className="link" disabled={!actor.trim() || busyId === id} onClick={() => void decide(item, "revoked")}>Revoke approval</button>}
        <details className="advanced-audit"><summary>Review history</summary><p>Proposed by: {item.proposed_by ?? "Not recorded"}</p><p>Source Events: {item.event_ids?.length ?? 0}</p>{item.approver && <p>Decision by {item.approver}{item.decided_at ? ` on ${String(item.decided_at).slice(0, 16).replace("T", " ")}` : ""}</p>}</details>
      </article>;
    })}</div>
  </section>;
}

