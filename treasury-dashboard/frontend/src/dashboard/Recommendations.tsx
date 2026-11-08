// Treasury recommendations (recommendation-agent/3): one recommendation set for the whole situation. The page opens
// with the prepared session; "Reassess" runs a live one for the ticked Events. Every statement carries its label
// (Fact / Engine / News forecast / AI assumption / To verify) with its source on hover. Nothing is executed.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { when } from "../format";
import { CONTEXT_LINE, costText, DECISIONS, defaultTicks, eventStatusText, factLabelOf, factLine, factPackText, forecastText,
         groupFacts, isLegacy, labelClass, moreText, originText, plural, positionOf, preparedLine, requestBody, sourceTip,
         triggerHelp, triggerText } from "./recommendationView";

function Stmt({ s, session }: { s: any; session: any }) {
  if (!s?.text) return null;
  return <span className="rec-stmt"><span className={`rec-lbl ${labelClass(s.label)}`} title={sourceTip(s, session)}>{s.label ?? "Fact"}</span> {s.text}</span>;
}

function EventList({ active, ticked, setTicked }: { active: any; ticked: string[]; setTicked: (t: string[]) => void }) {
  const [more, setMore] = useState(false);
  const toggle = (id: string) => setTicked(ticked.includes(id) ? ticked.filter(x => x !== id) : [...ticked, id]);
  const row = (e: any) => <li key={e.event_id}><label className="rec-event">
    <input type="checkbox" checked={ticked.includes(e.event_id)} onChange={() => toggle(e.event_id)} />
    <span className="rec-event-title">{e.title}</span>
    <span className="small muted">{e.category} · {when(e.published_at, false)} · <span className={`chip ${e.forecast === "model_saved" ? "ok" : ""}`}>{forecastText(e.forecast)}</span></span>
  </label></li>;
  if (!active) return <p className="small muted">Loading the active News Events…</p>;
  return <div className="rec-events">
    <p className="small muted">Active News Events, ranked by relevance. The top {active.included.length} are included; untick any you want left out.</p>
    <ul>{active.included.map(row)}</ul>
    {active.more_count > 0 && <>
      <button className="link small" aria-expanded={more} onClick={() => setMore(!more)}>{more ? "Hide" : "Show"} {moreText(active.more_count)}</button>
      {more && <ul className="rec-more">{active.not_included.map(row)}</ul>}
    </>}
  </div>;
}

export function DecisionCard({ d, session }: { d: any; session: any }) {
  const eff = d.expected_effect ?? {};
  const [review, setReview] = useState(false);
  return <article className="rec-card" aria-label={d.title}>
    <header className="rec-card-head">
      <span className="rec-rank">{d.rank}</span>
      <h3 className="rec-title">{d.title}</h3>
    </header>
    <div className="rec-section"><b>Cash at stake</b><Stmt s={d.cash_at_stake} session={session} /></div>
    <dl className="rec-facts">
      <div><dt>Owner</dt><dd>{d.owner?.role} <span className="small muted">(business function)</span></dd></div>
      <div><dt>By when</dt><dd>{d.timing?.text}</dd></div>
      <div><dt>Effort / reversibility</dt><dd>{d.effort} / {d.reversibility}</dd></div>
    </dl>
    <details className="rec-more-details"><summary>Why, effect and escalation</summary>
      <p className="small rec-trigger" title={triggerHelp(d.trigger?.type)}><b>Trigger:</b> {triggerText(d.trigger, when)}</p>
      <div className="rec-section"><b>Why it matters</b><ul>{(d.why ?? []).map((w: any, i: number) => <li key={i}><Stmt s={w} session={session} /></li>)}</ul></div>
      <div className="rec-section"><b>Expected effect</b>
        {eff.engine ? <span> Lowest headroom {eff.engine.min_headroom_change_eur_m >= 0 ? "+" : "−"}EUR {Math.abs(eff.engine.min_headroom_change_eur_m).toFixed(1)}m (tested, graded {String(eff.engine.grade).replace(/_/g, " ")}); gives up EUR {Math.abs(eff.engine.cost_if_reverses_over_horizon_eur_m ?? 0).toFixed(1)}m if the move reverses.</span>
          : <span className="muted"> Not quantified.</span>}</div>
      <div className="rec-section"><b>If wrong</b><Stmt s={d.if_wrong} session={session} /></div>
      <div className="rec-section"><b>Escalate if</b><ul>{(d.escalate_if ?? []).map((w: any, i: number) => <li key={i}><Stmt s={w} session={session} /></li>)}</ul></div>
      {d.usual_practice && <div className="rec-section"><b>How treasurers usually handle this</b><span> {d.usual_practice}</span></div>}
      {(d.event_ids ?? []).length > 0 && <div className="rec-section"><b>Evidence</b><ul>{d.event_ids.map((id: string) => {
        const f = session.facts?.[`F.news.${id}`];
        return <li key={id}>{f?.label ?? "News Event"}{(f?.articles ?? []).slice(0, 3).map((a: any) =>
          <span key={a.url}> · <a href={a.url} target="_blank" rel="noreferrer noopener">{a.publisher ?? "Source"}</a></span>)}</li>;
      })}</ul></div>}
    </details>
    <button className="link small rec-record" aria-expanded={review} onClick={() => setReview(!review)}>{review ? "Hide decision form" : "Record decision"}</button>
    {review && <DecisionReview sessionId={session.session_id} decisionId={d.decision_id} />}
  </article>;
}

function EventsUsed({ session }: { session: any }) {
  const checks = Object.fromEntries((session.to_verify ?? []).map((v: any) => [v.id, v.check]));
  if (!(session.events_used ?? []).length) return null;
  return <div className="rec-events-used"><h3>Events and how they were used</h3>
    <ul>{session.events_used.map((e: any) => <li key={e.event_id}>
      <b>{e.title ?? session.facts?.[`F.news.${e.event_id}`]?.label ?? "News Event"}</b>
      {" — "}<span className={`chip ${e.status === "used_in_decision" ? "ok" : e.status === "not_quantifiable" ? "warn" : ""}`}>{eventStatusText(e)}</span>
      {" "}{e.reason}{e.to_verify_id && checks[e.to_verify_id] ? <span className="small muted"> · To verify: {checks[e.to_verify_id]}</span> : null}
    </li>)}</ul></div>;
}

function DecisionReview({ sessionId, decisionId }: { sessionId: string; decisionId: string }) {
  const [reviewer, setReviewer] = useState(""), [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false), [msg, setMsg] = useState(""), [history, setHistory] = useState<any[]>([]);
  const load = () => api.recommendationDecisions(sessionId).then(r => setHistory((r.decisions ?? []).filter((x: any) => x.decision_id === decisionId))).catch(() => {});
  useEffect(() => { void load(); }, [sessionId, decisionId]);
  const decide = async (decision: string) => {
    setBusy(true); setMsg("");
    try { await api.recommendationDecision(sessionId, { decision_id: decisionId, decision, reviewer: reviewer.trim(), comment }); setComment(""); setMsg("Recorded in the audit log."); await load(); }
    catch (e: any) { setMsg(e?.message ?? String(e)); } finally { setBusy(false); }
  };
  return <div className="rec-review">
    <input className="inp" aria-label="Reviewer name" placeholder="Reviewer name" value={reviewer} onChange={e => setReviewer(e.target.value)} />
    <input className="inp" aria-label="Comment" placeholder="Comment" value={comment} onChange={e => setComment(e.target.value)} />
    <div className="rec-review-buttons">{DECISIONS.map(([k, label]) =>
      <button key={k} className="preset" disabled={busy || !reviewer.trim()} onClick={() => void decide(k)}>{label}</button>)}</div>
    {msg && <p className="small" role="status">{msg}</p>}
    {history.length > 0 && <ul className="small muted rec-history">{history.map(h => <li key={h.id}>{h.decision} · {h.reviewer} · {when(h.at)}{h.comment ? ` · ${h.comment}` : ""}</li>)}</ul>}
    <p className="small muted">Recording a decision changes no scenario or assumption and executes nothing.</p>
  </div>;
}

function HowReached({ session }: { session: any }) {
  const h = session.how_reached ?? {};
  const c = h.checks ?? {};
  return <details className="rec-how"><summary>How this was reached</summary>
    <p className="small">{originText(session)}. {plural(c.passed ?? 0, "item")} passed the checks{c.repaired ? ` after ${plural(c.repaired, "repair round")}` : ""}; {plural(c.removed ?? 0, "item")} removed. {plural(h.engine_runs ?? 0, "liquidity forecast run")}.</p>
    <p className="small"><b>AI cost:</b> {costText(session)}</p>
    <p className="small muted">{h.rounding_note}</p>
    <h4>Steps</h4>
    <ol className="small rec-steps">{(h.steps ?? []).map((st: any, i: number) => <li key={i}>{st.text}
      {(st.sent_back ?? []).length > 0 && <ul>{st.sent_back.map((x: any, j: number) =>
        <li key={j}><b>{x.item}</b>: {x.reason} <span className={`chip ${x.resolution === "removed" ? "bad" : "ok"}`}>{x.resolution}</span></li>)}</ul>}
    </li>)}</ol>
    {(h.removed_by_checks ?? []).length > 0 && <><h4>Removed by checks</h4><ul className="small">{h.removed_by_checks.map((x: any, i: number) =>
      <li key={i}><b>{x.title}</b> ({x.kind})</li>)}</ul></>}
    <h4>Facts used ({(h.facts_used ?? []).length})</h4>
    {groupFacts(h.facts_used).map(g => <div key={g.group} className="rec-fact-group"><h5>{g.group}</h5>
      <ul className="small">{g.facts.map((f: any) => <li key={f.id}><span className={`rec-lbl ${labelClass(factLabelOf(f.provenance))}`}>{factLabelOf(f.provenance)}</span> {factLine(f, when)}</li>)}</ul>
    </div>)}
    {h.note && <p className="small muted">{h.note}</p>}
  </details>;
}

export const HowReachedForTest = HowReached;

export function RecommendationsCard() {
  const [active, setActive] = useState<any>(null), [ticked, setTicked] = useState<string[]>([]);
  const [session, setSession] = useState<any>(null), [preparing, setPreparing] = useState(false);
  const [busy, setBusy] = useState(false), [elapsed, setElapsed] = useState(0), [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined), poll = useRef<number | undefined>(undefined);

  const loadLatest = async () => {
    const r = await api.recommendationLatest();
    setPreparing(!!r.preparing);
    if (r.session) setSession(r.session);
    return r;
  };
  useEffect(() => {
    api.activeEvents().then(a => { setActive(a); setTicked(defaultTicks(a)); }).catch(e => setError(e?.message ?? String(e)));
    loadLatest().then(r => { if (r.preparing) poll.current = window.setInterval(() => { void loadLatest().then(x => { if (!x.preparing) window.clearInterval(poll.current); }); }, 5000); })
      .catch(e => setError(e?.message ?? String(e)));
    return () => { window.clearInterval(timer.current); window.clearInterval(poll.current); };
  }, []);

  const reassess = async () => {
    setBusy(true); setError(null); setElapsed(0);
    const start = Date.now();
    timer.current = window.setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 1000);
    try { setSession(await api.recommendationAgent(requestBody(ticked))); }
    catch (e: any) { setError(e?.message ?? String(e)); }
    finally { window.clearInterval(timer.current); setBusy(false); }
  };

  const rate = session?.usd_per_eur?.value;
  const pos = positionOf(session?.position?.value);
  return <section className="card rec-board" aria-labelledby="rec-title">
    <div className="hd"><div><h2 id="rec-title">Treasury recommendations</h2>
      <p className="sub">One recommendation set for the whole situation: liquidity forecast, market moves, the ticked News and policy. For approval only; nothing is executed.</p></div></div>
    <EventList active={active} ticked={ticked} setTicked={setTicked} />
    <div className="rec-controls">
      <button className="primary" disabled={busy || !ticked.length} onClick={() => void reassess()}>{busy ? "Reassessing…" : "Reassess"}</button>
      {!session && <span className="small muted">{factPackText(active?.fact_pack, when)}</span>}
    </div>
    {busy && <p className="rec-progress" role="status"><span className="spinner" aria-hidden />Reassessing ({elapsed}s): building the facts, testing options and checking every item. Rule-based takes about 10 s; with the AI model about two minutes.</p>}
    {preparing && !session && <p className="small muted" role="status">Preparing the latest recommendations in the background…</p>}
    {error && <p className="small err" role="alert">{error}</p>}
    {session && isLegacy(session) && <p className="small muted">The latest saved session uses an older format and is not shown. Select Reassess.</p>}
    {session && !isLegacy(session) && <>
      <p className="small muted rec-stamp">{preparedLine(session, when)} · {originText(session)}{rate ? ` · EUR figures at USD ${rate} per EUR` : ""}</p>
      <div className={`rec-position ${pos.cls}`}>
        <span className={`chip ${pos.cls}`}>{pos.text}</span>
        <p>{(session.position?.headline ?? []).join(" ")}</p>
      </div>
      <p className="small muted rec-context">{CONTEXT_LINE}</p>
      {session.decisions.length === 0 ? <p className="small muted">No decision passed the checks; see How this was reached.</p>
        : <div className="rec-cards">{session.decisions.map((d: any) => <DecisionCard key={d.decision_id} d={d} session={session} />)}</div>}
      {session.rejected_alternatives?.length > 0 && <div className="rec-rejected"><h3>Considered, not recommended</h3>
        <ul>{session.rejected_alternatives.map((a: any, i: number) => <li key={i}><b>{a.title}</b> — <Stmt s={{ ...a.reason, label: a.reason?.label ?? "Engine" }} session={session} /></li>)}</ul></div>}
      <EventsUsed session={session} />
      {session.to_verify?.length > 0 && <div className="rec-verify"><h3>To verify before acting</h3>
        <ul>{session.to_verify.map((v: any) => <li key={v.id}><span className="rec-lbl lbl-verify">To verify</span> {v.check}</li>)}</ul></div>}
      <HowReached session={session} />
    </>}
  </section>;
}
