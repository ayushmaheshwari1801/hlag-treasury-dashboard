import { AgentBullets } from "../AgentBullets";
import { groupNewsCategories } from "../eventScenario";
// Drawers for News evidence, the aggregate assistant and data connections. Moved unchanged from the original dashboard.
import { useEffect, useState } from "react";
import { api } from "../api";
import { Drawer } from "../Drawer";
import { MarketGrid } from "../EnginePanels";
import { MODULE_NAME, shockText, when } from "../format";
import { groupNewsEvents } from "../newsEvents";
export function NewsList({ onClose, onOpen, onForecast, allowedEventIds, isPinned, onTogglePin, onLoaded }: { onClose: () => void; onOpen: (id: string) => void; onForecast: (eventId: string) => void; allowedEventIds: string[]; isPinned?: (eventId: string) => boolean; onTogglePin?: (row: any) => void; onLoaded?: (rows: any[], complete: boolean) => void }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.articles(50).then(x => { setD(x); onLoaded?.(groupNewsEvents(x.items ?? []), x.total != null && (x.items ?? []).length >= x.total); }).catch(() => setD({ items: [], message: "News could not be loaded." })); }, []);
  return <Drawer title="News tracker" onClose={onClose}>
    {!d ? <p className="muted">Loading{"…"}</p> : <>
      {d.status && <p className="small muted">News {d.status} {"·"} as of {when(d.as_of)}</p>}
      {groupNewsCategories<any>(groupNewsEvents(d.items ?? [])).map(group => <section key={group.category} className={`news-category category-${group.category}`}><h3 className="news-category-heading">{group.label}<span>{group.rows.length} events</span></h3>{group.rows.map((n: any) => <div key={n.id} className="tracker-event"><button className="news" onClick={() => onOpen(n.article_ids[0])}><i className={`dot ${n.score == null ? "pending" : "b"}`} /><div><div className="ntitle">{n.title}</div><div className="nmeta">Source published {when(n.published_at, false)} {"·"} {n.category} {"·"} Priority {n.score ?? "unscored"} · {n.article_ids.length} sources {n.modules.map((m: string) => <span key={m} className="chip"> · {MODULE_NAME[m] ?? m}</span>)}</div></div></button>{onTogglePin && <button className={`testbtn pin-btn${isPinned?.(n.event_id) ? " on" : ""}`} aria-pressed={!!isPinned?.(n.event_id)} onClick={() => onTogglePin(n)}>{isPinned?.(n.event_id) ? "Pinned ✓" : "Pin to tracker"}</button>}{allowedEventIds.includes(n.event_id) && <button className="testbtn" onClick={() => onForecast(n.event_id)}>Use as scenario</button>}</div>)}</section>)}
      {d.coverage_gaps?.length > 0 && <details><summary>Coverage gaps ({d.coverage_gaps.length})</summary><ul className="small">{d.coverage_gaps.map((g: any, i: number) => <li key={i}>{typeof g === "string" ? g : g.message ?? "Some news coverage is unavailable."}</li>)}</ul></details>}
      {d.message && <p className="muted small">{d.message}</p>}</>}
  </Drawer>;
}

const isDemoUrl = (u?: string) => { if (!u || /(^|\.)example\.(org|com|net)\b|\.test\b|synthetic/i.test(u)) return true; try { return !["http:", "https:"].includes(new URL(u).protocol); } catch { return true; } };
const SCORE_LABEL: Record<string, string> = { impact: "Impact", relevance: "Company relevance", urgency: "Urgency" };

function Section({ tone, icon, title, children }: { tone: string; icon: string; title: string; children: React.ReactNode }) {
  return <section className={`asec as-${tone}`}><h4><span className="as-ic" aria-hidden>{icon}</span>{title}</h4>{children}</section>;
}

export function ArticleDrawer({ id, onClose, onTest, onForecast, allowedEventIds }: { id: string; onClose: () => void; onTest: (t: any) => void; onForecast: (eventId: string) => void; allowedEventIds: string[] }) {
  const [a, setA] = useState<any>(null); const [err, setErr] = useState("");
  useEffect(() => { api.article(id).then(setA).catch(e => setErr(e.message)); }, [id]);
  return <Drawer title="Article" onClose={onClose}>
    {err && <p className="err">{err}</p>}{!a && !err && <p className="muted">Loading{"\u2026"}</p>}
    {a && <div className="article">
      <div className="a-head">
        <div className="a-badges">
          <span className="chip">{a.ai_analyzed ? "Reviewed" : "Unscored event"}</span>
          <span className="chip">{a.category}</span>
          <span className={`chip prio ${a.score >= 85 ? "bad" : a.score >= 75 ? "warn" : ""}`}>Priority {a.score ?? "—"}/100</span>
        </div>
        <h3 className="a-title">{a.title}</h3>
        <div className="a-meta">Article published {when(a.published_at, false)} {"\u00B7"} {a.source ?? "source unknown"}</div>
      </div>

      <Section tone="blue" icon={"\u2139"} title="Summary"><AgentBullets text={a.summary} /></Section>
      {allowedEventIds.includes(a.event_id) && <button className="testbtn" onClick={() => onForecast(a.event_id)}>Use this Event as a scenario</button>}

      {a.facts?.length > 0 && <Section tone="grey" icon={"\u2713"} title="Key facts">
        <AgentBullets text={a.facts} /></Section>}

      <Section tone="purple" icon={"\u21C4"} title="Treasury impact">
        {a.modules?.length > 0
          ? <p className="a-areas">Affects: {a.modules.map((m: string) => <span key={m} className="chip hlchip">{MODULE_NAME[m] ?? m}</span>)}</p>
          : <p className="muted small">No sensitivity area matched. Context only.</p>}
        {a.treasury_pathways?.length > 0 && <><div className="a-sub">Pathway</div><AgentBullets text={a.treasury_pathways} /></>}
      </Section>

      {a.recommended_actions?.length > 0 && <Section tone="green" icon={"\u2192"} title="Treasury considerations">
        <AgentBullets text={a.recommended_actions} /></Section>}

      {a.scores && <Section tone="amber" icon={"\u2605"} title="Why this priority">
        {Object.keys(SCORE_LABEL).filter(k => a.scores[k] != null).map(k => (
          <div key={k} className="a-score">
            <div className="a-score-top"><span>{SCORE_LABEL[k]}</span><b>{a.scores[k]}/5</b></div>
            <div className="a-bar"><i style={{ width: `${a.scores[k] / 5 * 100}%` }} /></div>
            {a.score_reasons?.[k] && <AgentBullets className="small muted" text={a.score_reasons[k]} />}
          </div>))}
        <p className="small muted">Priority reflects potential impact, company relevance and urgency. Each event is counted once.</p>
      </Section>}

      <Section tone="slate" icon={"\u{1F517}"} title="Sources">
        {(a.sources?.length ? a.sources : [{ url: a.url, title: a.title, source: a.source }]).map((s: any, i: number) => (
          <div key={i} className="a-src">
            {isDemoUrl(s.url)
              ? <><span className="a-src-title">{s.title}</span><span className="chip warn">demo source, not a live link</span></>
              : <a href={s.url} target="_blank" rel="noreferrer noopener" className="a-src-title">{s.title} {"\u2197"}</a>}
            <div className="small muted">{s.source}{s.published_at ? ` \u00B7 ${when(s.published_at, false)}` : ""}</div>
            {s.text && <details className="original-text"><summary>Read original article</summary><div>{s.text}</div></details>}
          </div>))}
        {(a.sources ?? []).some((s: any) => isDemoUrl(s.url)) && <p className="small muted">The bundled demo articles are synthetic test data, so their links do not open real pages. Articles from a live news-engine export link to the original source.</p>}
      </Section>
      <p className="small muted">News is context for review. Company exposure and cash timing require confirmation.</p>
    </div>}
  </Drawer>;
}

function ArticleCitation({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  const [title, setTitle] = useState("Source article — loading title…");
  useEffect(() => {
    let active = true;
    api.article(id).then(a => { if (active) setTitle(a.title || "Source article — title unavailable"); })
      .catch(() => { if (active) setTitle("Source article — title unavailable"); });
    return () => { active = false; };
  }, [id]);
  return <button className="chip linkchip" onClick={() => onOpen(id)}>{title} ›</button>;
}

const SUGGESTED = ["What is the biggest cash risk right now?", "How close are we to the minimum liquidity?", "What should I look at in the news today?", "How do slower collections change our headroom?"];

export function AssistantDrawer({ months, shocks, forecastInput, overrides, onClose, onOpenArticle }: { months: number; shocks: Record<string, number>; overrides: Record<string, number | boolean | string>; forecastInput?: {forecast_id?: string; scenario: any; as_of?: string}; onClose: () => void; onOpenArticle: (id: string) => void }) {
  const [status, setStatus] = useState<any>(null);
  const [b, setB] = useState<any>(null);
  const [q, setQ] = useState("");
  const [chat, setChat] = useState<{ q: string; a?: any; err?: string }[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.assistantStatus().then(setStatus).catch(() => setStatus(null)); api.brief().then(setB).catch(() => setB(null)); }, []);
  const send = async (question: string, mode: "question" | "briefing") => {
    if (busy) return;
    const label = mode === "briefing" ? "Morning briefing" : question;
    setChat(c => [...c, { q: label }]); setBusy(true); setQ("");
    try { const a = await api.assistant(question, mode, months, shocks, forecastInput, overrides); setChat(c => c.map((x, i) => i === c.length - 1 ? { ...x, a } : x)); }
    catch (e: any) { setChat(c => c.map((x, i) => i === c.length - 1 ? { ...x, err: e.message } : x)); }
    finally { setBusy(false); }
  };
  const custom = Object.keys(shocks).length > 0 || !!forecastInput || Object.keys(overrides).length > 0;
  return <Drawer title="Assistant" onClose={onClose}>
    <div className="as-status">
      {status?.mode === "model" ? <span className="chip ok">Treasury assistant</span> : <span className="chip warn">Dashboard summary</span>}
      <span className="small muted">Next {months} months{custom ? " \u00B7 what-if scenario applied" : ""}</span>
    </div>
    <button className="primary wide-btn" onClick={() => send("", "briefing")} disabled={busy}>{"\u2726"} Generate my morning briefing</button>
    <div className="chat">
      {chat.length === 0 && <div className="suggest">{SUGGESTED.map(s => <button key={s} className="preset" onClick={() => send(s, "question")} disabled={busy}>{s}</button>)}</div>}
      {chat.map((m, i) => <div key={i} className="turn">
        <div className="q">{m.q}</div>
        {!m.a && !m.err && <div className="a muted">Thinking{"\u2026"}</div>}
        {m.err && <div className="a err">{m.err}</div>}
        {m.a && <div className={`a ${m.a.guard?.passed === false ? "a-warn" : ""}`}>
          <AgentBullets className="a-text" text={m.a.answer} />
          {(m.a.cited_rule_ids.length > 0 || m.a.cited_article_ids.length > 0) && <div className="cites">
            <span className="small muted">Supporting sources</span>
            {m.a.cited_article_ids.map((id: string) => <ArticleCitation key={id} id={id} onOpen={onOpenArticle} />)}
          </div>}
          <div className="a-meta">{m.a.source === "model" ? "Based on the current dashboard" : "Dashboard summary; extended analysis unavailable"}</div>
        </div>}
      </div>)}
    </div>
    <div className="ask">
      <input className="inp wide" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && q.trim()) send(q, "question"); }}
        placeholder={"Ask about today's numbers\u2026"} aria-label="Question" maxLength={2000} />
      <button className="primary" onClick={() => send(q, "question")} disabled={!q.trim() || busy}>Ask</button>
    </div>
    <p className="small muted">Answers use the figures and assumptions on this dashboard. Proposed actions are for Treasury review.</p>
    {b?.available && <details className="brief-src"><summary>{b.week_start && b.week_end ? `Weekly news brief (${b.week_start} to ${b.week_end})` : "News analysis (cumulative, not a weekly brief)"}</summary><AgentBullets text={b.executive_summary} />
      {b.coverage_gaps?.length > 0 && <ul className="small muted">{b.coverage_gaps.map((g: any, i: number) => <li key={i}>{typeof g === 'string' ? g : g.message ?? "Some news coverage is unavailable."}</li>)}</ul>}</details>}
  </Drawer>;
}

export function SourcesDrawer({ onClose, reload }: { onClose: () => void; reload: () => void }) {
  const [d, setD] = useState<any>(null); const [actor, setActor] = useState(""); const [msg, setMsg] = useState<string | null>(null);
  const refresh = () => api.integrations().then(setD);
  useEffect(() => { refresh(); }, []);
  const reset = async (k: string) => { try { await api.deleteSource(k, actor); setMsg("Restored the demo default."); await refresh(); reload(); } catch (e: any) { setMsg(e.message); } };
  return <Drawer title="Data connections" onClose={onClose} wide modal>
    <p className="small muted">Review data availability and refresh market observations. Demo data is illustrative.</p>
    {!d ? <p className="muted">Loading{"…"}</p> : d.integrations.filter((i: any) => i.id !== "execution").map((i: any) => (
      <div key={i.id} className="integ"><div><b>{({news_engine: "News coverage", cash_market: "Market data", company_profile: "Company financial profile", assistant: "Treasury assistant"} as Record<string,string>)[i.id] ?? i.name}</b> <span className={`chip ${i.status === "connected" ? "ok" : i.status === "demo" ? "warn" : ""}`}>{i.id === "assistant" ? (i.status.startsWith("connected") ? "Available" : "Dashboard summary only") : i.id === "company_profile" ? "Read-only" : i.status}</span></div>
        
        {i.id.startsWith("data_") && i.status === "connected" && <button onClick={() => reset(i.id.slice(5))} disabled={!actor.trim()}>Restore demo default</button>}</div>))}
    {d?.integrations.some((i: any) => i.id.startsWith("data_") && i.status === "connected") && <input className="inp wide" placeholder="Your name (needed to restore a default)" value={actor} onChange={e => setActor(e.target.value)} aria-label="Your name" />}
    {msg && <p className="small" role="status">{msg}</p>}
    <MarketGrid onRefreshed={() => { void refresh(); reload(); }} />
  </Drawer>;
}



