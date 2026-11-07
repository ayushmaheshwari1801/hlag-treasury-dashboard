// The board's right-hand column from the uploaded frontend: each News Event and its own cash impact (a what-if run
// with only that Event's forecast inputs) on one aligned row and its detail page.
// Figures come from the dashboard API; the browser only formats them.
import { type KeyboardEvent, type ReactNode } from "react";
import { Drawer } from "../Drawer";
import { ForecastSummary } from "../ForecastSummary";
import { eur, MODULE_NAME, num, ROW_LABEL, shockText, when } from "../format";
import { riskStatus } from "../Insights";
import { groupNewsCategories } from "../eventScenario";
import NewsRefreshProgress from "../NewsRefreshProgress";
import { pctOfHeadroom } from "../engineUi";
import { inputChange, inputLabel } from "./metricAssumptions";
import { LiquidityLinesChart } from "./LiquidityForecast";
import { type RiskView } from "./MarketCards";
import type { EventImpact, EventImpacts, PathPoint } from "./useEventImpact";
import type { MarketRisk } from "./useMarketRisk";

export interface NewsRow { id: string; event_id: string; title: string; published_at?: string; source?: string; category?: string; score?: number | null; article_ids: string[]; modules: string[]; scenario_tests?: any[] }

const keyAct = (fn: () => void) => (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } };
const tone = (v: number | null | undefined) => (v == null ? "" : v < -0.05 ? "neg" : v > 0.05 ? "pos" : "");
const inputsText = (forecast: any) => (forecast?.predictions ?? []).map((p: any) => `${inputLabel(p.param_key)} ${inputChange(p)}`).join(" · ");
const noInputsText = (_forecast: any) => "Monitor · cash impact not quantified";

/** One row per News Event: the News on the left, its own cash impact on the right, always at the same height. */
export function NewsImpactCard({ rows, r, impacts, colors, onOpenNews, onOpenImpact, onSeeAll }:
  { rows: NewsRow[]; r: MarketRisk; impacts: EventImpacts; colors: Record<string, string>; onOpenNews: (row: NewsRow) => void; onOpenImpact: (row: NewsRow) => void; onSeeAll: () => void }) {
  const { data, collecting, collectionStatus, collectionMessage } = r;
  const running = collecting || collectionStatus?.running;
  return <section className="card news-impact" aria-labelledby="ni-news ni-impact">
    <div className="ni-head">
      <div><div className="hd"><h2 id="ni-news">News Tracker</h2><div className="news-tools">
        <button className="link" disabled={running} onClick={r.collectNews} title="Fetch and review the latest news">{running ? "Collecting…" : "Refresh news"}</button>
        <button className="link" onClick={onSeeAll}>See all</button></div></div>
        <p className="sub">Top Events per category · click a row for its evidence</p>
        <div className="colhead"><span>NEWS EVENT · SOURCE · PRIORITY</span></div></div>
      <div><div className="hd"><h2 id="ni-impact">Event impact on cash</h2></div><p className="sub">If only this News happened · next {r.months} months (EUR million)</p>
        {impacts.failed > 0 && <button className="link" disabled={impacts.running} onClick={impacts.retry}>Retry unavailable estimates ({impacts.failed})</button>}
        <div className="colhead"><span>NET CASH IMPACT · FORECAST INPUTS</span></div></div>
    </div>
    {!rows.length && <p className="muted small">No saved News Events yet. Refresh news to collect them.</p>}
    <div className="ni-rows">{groupNewsCategories(rows).map(group => <section key={group.category} className={`news-category category-${group.category}`} aria-label={`${group.label} news`}>
      <h3 className="news-category-heading">{group.label}<span>{group.rows.length} events</span></h3>
      {!group.rows.length && <p className="small muted">No news in this category for the current selection.</p>}
      {group.rows.map(row => {
      const item = impacts.items[row.event_id];
      return <div className="ni-row" key={row.event_id}>
        <div className="ni-news news" role="button" tabIndex={0} onClick={() => onOpenNews(row)} onKeyDown={keyAct(() => onOpenNews(row))} aria-label={`Open ${row.title}`}>
          <i className="dot category-dot" title={group.label} />
          <div><div className="ntitle">{row.title}</div><div className="nmeta">{row.published_at ? when(row.published_at, false) : ""}{row.source ? ` · ${row.source}` : ""}<span className="chip">Priority {row.score ?? "—"}</span><span className="muted"> · {row.article_ids.length} sources</span></div></div>
          <span className="chev" aria-hidden>›</span>
        </div>
        <div className="ni-impact" style={item?.status === "ready" ? { borderLeftColor: colors[row.event_id] } : undefined} role="button" tabIndex={0} aria-label={`Impact of ${row.title}`}
          onClick={() => onOpenImpact(row)} onKeyDown={keyAct(() => onOpenImpact(row))}>
          <ImpactSummary item={item} />
          <span className="chev" aria-hidden>›</span>
        </div>
      </div>;
    })}</section>)}</div>
    {rows.length > 0 && <p className="foot">Each figure is a separate what-if run with only that Event's forecast inputs (assumptions, not observed prices) and does not add up with the others. Net cash impact over the selected horizon.</p>}
    {!data.news.available && <p className="muted small">{data.news.message}</p>}
    {running && <NewsRefreshProgress stage={r.collectionStage} status={collectionStatus?.running ? collectionStatus : undefined} />}
    {!running && (collectionMessage || collectionStatus?.run_id) && <p className="news-refresh-message small" role="status">{collectionMessage || `Last news collection: ${collectionStatus.status}.`}</p>}
    {!collectionStatus?.running && collectionStatus?.warnings?.length > 0 && <details className="news-collection-errors small">
      <summary>{collectionStatus.warnings.length} collection warnings</summary>
      <ol>{collectionStatus.warnings.map((w: any, i: number) => <li key={i}><p>Some proposed news groupings were excluded from this assessment.</p></li>)}</ol>
    </details>}
    {!collectionStatus?.running && collectionStatus?.errors?.length > 0 && <details className="news-collection-errors small">
      <summary>{collectionStatus.errors.length} collection errors</summary>
      <ol>{collectionStatus.errors.map((e: any, i: number) => <li key={i}><p>Some news could not be assessed. Previously saved news remains available.</p></li>)}</ol>
    </details>}
  </section>;
}

function ImpactSummary({ item }: { item?: EventImpact }) {
  if (!item || item.status === "queued") return <span className="muted small">Waiting…</span>;
  if (item.status === "needs_model") return <span className="muted small">Not estimated yet</span>;
  if (item.status === "forecasting") return <span className="rec-progress small"><span className="spinner" aria-hidden />Inferring forecast inputs…</span>;
  if (item.status === "calculating") return <span className="rec-progress small"><span className="spinner" aria-hidden />Calculating cash impact…</span>;
  if (item.status === "error") return <span className="small err">Could not be calculated: {item.error}</span>;
  if (item.status === "no_inputs") return <span className="muted small">{noInputsText(item.forecast)}</span>;
  const net = item.finance?.cash_impact?.net_eur_m;
  return <div className="ni-event">
    {net == null ? <b className="muted">Incomplete</b> : <b className={`ni-amount ${tone(net)}`}>{eur(net)}</b>}
    <small>{inputsText(item.forecast)}</small>
    {net == null && <small>Required inputs are missing for at least one driver; partial rows are never summed.</small>}
  </div>;
}

const Box = ({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) =>
  <section className={`dx-box ${className}`}><h3>{title}</h3>{children}</section>;

/** Detail for one News Event: everything here is the what-if run with only this News Event's forecast inputs. */
export function NewsImpactDetail({ row, item, color, r, v, baseline, reserve, onClose, onOpenArticle, onApply }:
  { row: NewsRow; item?: EventImpact; color?: string; r: MarketRisk; v: RiskView; baseline: PathPoint[]; reserve: number | null; onClose: () => void; onOpenArticle: (id: string) => void; onApply: () => void }) {
  const forecast = item?.forecast, fin = item?.finance, ci = fin?.cash_impact, lq = fin?.liquidity, calc = fin?.calculation;
  const predictions: any[] = forecast?.predictions ?? [];
  const status = fin ? riskStatus(lq, calc) : null;
  const rate = v.rate;
  const headroomChange = calc?.scenario_min_usd_m != null && calc?.baseline_min_usd_m != null ? (calc.scenario_min_usd_m - calc.baseline_min_usd_m) / rate : null;
  const applied = r.forecastActive && r.selectedEventId === row.event_id;
  return <Drawer title={row.title} modal wide onClose={onClose}>
    <div className="dx">
      <header className="dx-top">
        <div><p className="sub">News Event{row.published_at ? ` · Latest source ${when(row.published_at, false)}` : ""}{row.source ? ` · ${row.source}` : ""} · scenario: only this News</p>
          {row.modules.length > 0 && <p className="a-areas small">Affects: {row.modules.map(m => <span key={m} className="chip hlchip">{MODULE_NAME[m] ?? m}</span>)}</p>}</div>
        <div><span>Net cash impact (next {r.months} months)</span>{ci?.net_eur_m != null ? <b className={`dx-big ${tone(ci.net_eur_m)}`}>{eur(ci.net_eur_m)}</b> : <b className="dx-big muted">{item?.status === "no_inputs" ? "Not quantified" : "Not assessed"}</b>}</div>
        <div><span>Lowest headroom</span>{lq ? <><b className={lq.scenario_headroom_eur_m < 0 ? "neg" : ""}>€{num(lq.scenario_headroom_eur_m, 0)}m</b><small> {when(lq.scenario_min_date, false)}</small></> : <b>—</b>}</div>
        <div><span>Priority</span><b>{row.score ?? "—"}/100</b></div>
      </header>
      {item?.status === "error" && <p className="small err" role="alert">This Event could not be calculated: {item.error}</p>}
      <div className="dx-cols">
        <div>
          {forecast && <div className="dx-info"><ForecastSummary forecast={forecast} /></div>}
          <Box title="Methodology"><table className="t dx-table"><tbody>
            <tr><th>Scenario</th><td>Only this news · next {r.months} months.</td></tr>
            {forecast && <tr><th>Forecast inputs</th><td>{forecast.mode === "model" ? "News-based estimate" : "Standard assumptions"}</td></tr>}
            {predictions.map((p, i) => <tr key={i}><th>{inputLabel(p.param_key)}</th>
              <td><b>{inputChange(p)}</b> from {when(p.effective_date, false)} for {p.duration_days} days · {Math.round(p.confidence * 100)}% confidence</td></tr>)}
            {forecast && !predictions.length && <tr><th>Mapping</th><td>{noInputsText(forecast)}, so no cash flow was adjusted. That is not evidence of no risk.</td></tr>}
          </tbody></table></Box>
          {ci && <Box title="Cash impact by driver"><table className="t dx-table"><tbody>
            {ci.rows.map((x: any) => <tr key={x.id}><th>{x.label ?? ROW_LABEL[x.id]}</th><td className={`r ${tone(x.impact_eur_m)}`}>{x.impact_eur_m == null ? "—" : eur(x.impact_eur_m)}</td></tr>)}
          </tbody></table></Box>}
          {ci && <div className={`dx-impact ${tone(ci.net_eur_m)}`}><div>
            <span>Estimated net cash impact · next {r.months} months</span>{ci.net_eur_m != null ? <b className={`dx-big ${tone(ci.net_eur_m)}`}>{eur(ci.net_eur_m)}</b> : <b>Incomplete</b>}
            <small>Adverse rows {eur(ci.adverse_total_eur_m)}{ci.adverse_pct_of_headroom != null ? ` · ${pctOfHeadroom(ci.adverse_pct_of_headroom)}` : ""}</small></div></div>}
        </div>
        <div>
          {predictions.length > 0 && <Box title="Liquidity path · baseline vs only this News">
            {item?.path && item.path.length > 1 && baseline.length > 1
              ? <LiquidityLinesChart compact baseline={baseline} reserve={reserve} lines={[{ id: row.event_id, label: "Only this News", color: color ?? "var(--navy)", points: item.path }]} />
              : <p className="small muted">{item?.status === "ready" ? "The liquidity forecast is unavailable for this assessment." : "Shown once the impact is calculated."}</p>}
          </Box>}
          {fin && <Box title="Liquidity context" className="dx-context">
            <div><span>Lowest liquidity</span><b>€{num(lq.scenario_eur_m, 0)}m</b><small>{when(lq.scenario_min_date, false)}</small></div>
            <div><span>Min. headroom vs baseline</span><b className={tone(headroomChange)}>{headroomChange == null ? "—" : eur(headroomChange)}</b><small>baseline</small></div>
            <div><span>Risk status</span><b>{status?.label ?? "—"}</b><small>{status?.incomplete ? "inputs incomplete" : status?.firstBreach}</small></div></Box>}
          <Box title="Assumptions & sources" className="dx-sources">
            {forecast?.assumption_notice && <ul><li>Estimates are scenario assumptions.</li><li>No hedge, payment or baseline change is made.</li></ul>}
            {row.scenario_tests?.map((t: any) => <p key={t.slider} className="small muted">Slider test: {t.slider_label} {shockText(t.value, t.unit)} · {t.caveat}</p>)}
            <p><b>Sources:</b></p><ul>
              {row.article_ids.map((id, i) => <li key={id}><button className="link" onClick={() => onOpenArticle(id)}>Original article {row.article_ids.length > 1 ? i + 1 : ""}</button></li>)}
              {(forecast?.evidence_links ?? []).map((url: string) => <li key={url} className="small"><a href={url} target="_blank" rel="noreferrer noopener">{url}</a></li>)}
            </ul>
          </Box>
        </div>
      </div>
      <footer className="dx-foot"><span className="small muted">Hypothetical scenario built from saved News; forecast inputs are assumptions, not observed prices or company disclosures.</span>
        <button className="primary" onClick={onClose}>Close</button></footer>
    </div>
  </Drawer>;
}

