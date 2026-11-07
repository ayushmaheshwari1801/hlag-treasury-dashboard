// Cards of the dashboard run, moved from the original dashboard with their markup and interactions intact.
// Layout follows the uploaded frontend; wording keeps the original terms (this run is the dashboard's main scenario).
import type { KeyboardEvent } from "react";
import { DebtChart, Sparkline, SignedBar, debtColor } from "../charts";
import { Prov } from "../Drawer";
import { cashImpactTone, eur, METRIC_LABEL, METRIC_SUB, marketArrow, metricChange, metricValue, MINUS, moduleOf, num, ROW_LABEL, shockText, when } from "../format";
import { Monitoring, Waterfall } from "../Insights";
import { groupNewsEvents } from "../newsEvents";
import { topEventScenarios } from "../eventScenario";
import { headroomAtRiskTip, memoLine, memoOnlyText, pctOfHeadroom, topActions } from "../engineUi";
import type { MarketRisk } from "./useMarketRisk";


export const ActionIcon = ({ sev }: { sev: string }) => <span className={`aicon sev-${sev.toLowerCase()}`} aria-hidden>{sev === "LOW" ? "i" : "!"}</span>;

/** Derived, display-only values shared by the market cards (requires loaded dashboard data). */
export function riskView(r: MarketRisk) {
  const data = r.data, f = data.finance, ci = f.cash_impact, lq = f.liquidity, bf = r.baseline?.finance;
  const baseRows: Record<string, number | null> = Object.fromEntries((bf?.cash_impact.rows ?? []).map((x: any) => [x.id, x.impact_eur_m]));
  const cashImpacts: Record<string, number | null> = Object.fromEntries(ci.rows.map((x: any) => [x.id, x.impact_eur_m]));
  const focus = r.focus, fm = focus?.modules ?? [];
  const newsAll: any[] = groupNewsEvents([...(data.news.items ?? []), ...(data.news.scenario_events ?? [])]);
  const scenarioEvents = topEventScenarios(data.news.scenario_events ?? (r.eventOptions.length ? r.eventOptions : newsAll));
  const newsPool = scenarioEvents.map(e => newsAll.find(n => n.event_id === e.event_id)).filter(Boolean);
  return {
    f, ci, lq, bf, baseRows, cashImpacts, fm, newsAll, scenarioEvents,
    newsShown: focus?.kind === "metric" ? newsPool.filter(n => n.modules.some((m: string) => fm.includes(m))) : newsPool,
    rate: lq.balance_date_eurusd as number,
    cls: (module: string) => (!focus ? "" : fm.includes(module) ? " hl" : " dim"),
    pickMetric: (id: string) => { const m = moduleOf(id); r.setFocus(focus?.kind === "metric" && focus.id === id ? null : { kind: "metric", id, modules: [m] }); },
    pickNews: (n: any) => r.setFocus(focus?.kind === "news" && focus.id === n.id ? null : { kind: "news", id: n.id, modules: n.modules }),
    sliderOf: (id: string) => f.scenario.sliders.find((s: any) => s.id === id),
    actionCards: topActions(f.all_actions ?? f.recommended_actions),
  };
}
export type RiskView = ReturnType<typeof riskView>;
const keyAct = (fn: () => void) => (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } };

/** One market metric: value, move, sparkline, how it reaches cash and its key model assumptions. */
function MetricCell({ m, r, v, onDetails }: { m: any; r: MarketRisk; v: RiskView; onDetails: (id: string) => void }) {
  const focus = r.focus, tone = cashImpactTone(v.cashImpacts[m.id]);
  return <div className={`metric ${tone ?? ""}${v.cls(m.module)}${focus?.kind === "metric" && focus.id === m.id ? " sel" : ""}`}
    role="button" tabIndex={0} aria-pressed={focus?.kind === "metric" && focus.id === m.id}
    onClick={() => onDetails(m.id)} onKeyDown={keyAct(() => onDetails(m.id))}>
    <div className="mname">{METRIC_LABEL[m.id]}{METRIC_SUB[m.id] && <small>{METRIC_SUB[m.id]}</small>}</div>
    <div className="mrow"><span className="mval">{metricValue(m.id, m.value).split(" / ")[0]}{metricValue(m.id, m.value).includes(" / ") && <small> / {metricValue(m.id, m.value).split(" / ")[1]}</small>}</span>
      <span className={`mchg ${tone ?? ""}`} title={`Week ending ${m.date ?? "unknown"}`}>{metricChange(m)} {marketArrow(m)}</span></div>
    <small className="quote-date">Week ending {when(m.date, false)}</small>
    {m.unusual?.available && <span className={`uchip u-${m.unusual.label}`} data-tip={`Is this move unusual?\n${m.unusual.text}.\n${m.unusual.method}`}>{m.unusual.z != null ? `${Math.abs(m.unusual.z).toFixed(1)}σ · ` : "first move in a year · "}{m.unusual.label}</span>}
    <div onClick={e => e.stopPropagation()}><Sparkline points={m.sparkline} tone={tone} fmt={(x: number) => metricValue(m.id, x)} source={`${m.source} (${m.source_kind})`} /></div>
    <button className="chev btn" onClick={e => { e.stopPropagation(); onDetails(m.id); }} aria-label={`${METRIC_LABEL[m.id]} details`} title="Details">{"›"}</button>
    {m.freshness !== "FRESH" && <span className="chip warn stale">{m.freshness}</span>}
  </div>;
}

/** One driver's estimated cash-flow impact from the market simulation. */
function ImpactCell({ x, r, v, maxAbs, onDetails }: { x: any; r: MarketRisk; v: RiskView; maxAbs: number; onDetails: (id: string) => void }) {
  const was = v.baseRows[x.id], changed = r.custom && was != null && x.impact_eur_m != null && Math.abs(was - x.impact_eur_m) >= 0.05;
  const sl = v.sliderOf(x.id);
  const memoOnly = memoOnlyText(x.memo), memo = memoLine(x.memo);
  const tip = `${x.label ?? ROW_LABEL[x.id]}: ${memoOnly || eur(x.impact_eur_m)}${memo ? `\n${memo}, at ${x.memo.horizon_end}; position × FX move, not on the liquidity path (D19)` : ""}\nScenario: ${sl ? shockText(sl.value, sl.unit) : x.scenario}\n${x.impact_type}\nStatus ${x.status} · ${x.provenance}${changed ? `\nObserved-move value: ${eur(was)}` : ""}`;
  const open = () => onDetails(x.id);
  return <div className={`cfrow${v.cls(x.module)}`} role="button" tabIndex={0} onClick={open} onKeyDown={keyAct(open)}>
    <div className="cfname">{x.label ?? ROW_LABEL[x.id]}{!x.cash && <small> annualised earnings proxy</small>}<span className="shock">{sl ? shockText(sl.value, sl.unit) : ""}</span>{memo && <small className="memo-line">{memo}</small>}</div>
    {memoOnly ? <div className="cfval memo-only">{memoOnly}</div>
      : <div className={`cfval ${cashImpactTone(x.impact_eur_m) ?? ""}`}>{eur(x.impact_eur_m)}{changed && <small className="was">was {eur(was)}</small>}</div>}
    <SignedBar value={memoOnly ? null : x.impact_eur_m} max={maxAbs} tip={tip} />
    <div className="har">{memoOnly || x.headroom_at_risk_pct == null || x.headroom_at_risk_pct === 0 ? "—" : `${x.headroom_at_risk_pct.toFixed(1)}%`}<small>headroom at risk</small></div>
    <button className="chev btn" onClick={e => { e.stopPropagation(); onDetails(x.id); }} aria-label={`${x.label ?? ROW_LABEL[x.id]} details`} title="Details">{"›"}</button>
  </div>;
}

/** Direct cash flows the scenario states (e.g. a fine named by a News Event): on the liquidity path and in the
 *  totals, but not a market-driver row. Shown only when the run has some within the selected horizon. */
function DirectCashRow({ d, maxAbs }: { d: any; maxAbs: number }) {
  const tip = `${d.note}\n${d.flows.map((x: any) => `${x.date} · ${x.label}: ${eur(x.amount_eur_m)}\n${x.rationale}`).join("\n")}`;
  return <div className="mi-row direct-cash"><div className="metric mi-empty"><div className="mname">Direct cash <span className="chip warn">model estimate</span></div>
      <ul className="direct-flows small">{d.flows.map((x: any) => <li key={`${x.effect_id}-${x.date}`} title={x.rationale}>{x.label} · {x.date} · {eur(x.amount_eur_m)}</li>)}</ul></div>
    <div className="cfrow"><div className="cfname">Direct cash flows <small>stated in the scenario, not a market move</small></div>
      <div className={`cfval ${cashImpactTone(d.impact_eur_m) ?? ""}`}>{eur(d.impact_eur_m)}</div>
      <SignedBar value={d.impact_eur_m} max={maxAbs} tip={tip} /><div className="har">—<small>headroom at risk</small></div><span /></div></div>;
}

function ImpactTotal({ v }: { v: RiskView }) {
  const ci = v.ci;
  return <div className="total" data-tip={ci.note}>
    <div><div className="tl">Total Estimated Impact</div><div className="tv">{num(ci.adverse_total_eur_m, 1)}</div><small>EUR million, adverse rows</small></div>
    <div className="tnet"><small>Net of favourable</small><div>{eur(ci.net_eur_m)}</div>
      <small>{ci.overlap_adjustment_eur_m ? `incl. surcharge overlap ${eur(ci.overlap_adjustment_eur_m)}` : ""}</small>
      <small>{pctOfHeadroom(ci.adverse_pct_of_headroom)}</small></div>
  </div>;
}

/** Market metrics and their estimated cash-flow impact on one aligned row each; every cell opens the metric's
 *  explanation with its model assumptions. Drivers without a market metric (e.g. CAD) follow below. */
export function MarketImpactCard({ r, v, onDetails, onCashDetails }: { r: MarketRisk; v: RiskView; onDetails: (id: string) => void; onCashDetails: (id: string) => void }) {
  const { f, ci } = v, months = r.months;
  const direct = ci.direct?.flows?.length ? ci.direct : null;
  const maxAbs = Math.max(...ci.rows.map((x: any) => Math.abs(x.impact_eur_m ?? 0)), Math.abs(direct?.impact_eur_m ?? 0), 1);
  const metricIds = new Set(v.f.market_metrics.map((m: any) => m.id));
  const extra = ci.rows.filter((x: any) => !metricIds.has(x.id));
  return <section className="card market-impact" aria-labelledby="mi-m mi-c">
    <div className="mi-head">
      <div><h2 id="mi-m">Market Metrics</h2><p className="sub">Latest values and 1-week change · click to see the drivers</p>
        <div className="colhead"><span>METRIC · 1-WEEK CHANGE</span></div></div>
      <div><h2 id="mi-c">Estimated Cash-Flow Impact</h2><p className="sub">Next {months} months (EUR million){r.custom && <span className="chip warn">scenario</span>}</p>
        <div className="colhead"><span>EXPOSURE / CASH IMPACT</span><span className="info-tip" data-tip={headroomAtRiskTip(months, f.calculation?.horizon_end)} tabIndex={0} aria-label={headroomAtRiskTip(months, f.calculation?.horizon_end)}>HEADROOM AT RISK <i aria-hidden>ⓘ</i></span></div></div>
    </div>
    <div className="mi-rows">
      {v.f.market_metrics.map((m: any) => { const x = ci.rows.find((row: any) => row.id === m.id);
        return <div className="mi-row" key={m.id}>
          <MetricCell m={m} r={r} v={v} onDetails={onDetails} />
          {x ? <ImpactCell x={x} r={r} v={v} maxAbs={maxAbs} onDetails={onCashDetails} /> : <div className="cfrow mi-empty"><span className="muted small">No cash-flow row for this metric</span></div>}
        </div>; })}
      {extra.map((x: any) => <div className="mi-row" key={x.id}><div className="metric mi-empty"><div className="mname">{x.label ?? ROW_LABEL[x.id]}</div></div>
        <ImpactCell x={x} r={r} v={v} maxAbs={maxAbs} onDetails={onCashDetails} /></div>)}
      {direct && <DirectCashRow d={direct} maxAbs={maxAbs} />}
    </div>
    <div className="mi-row mi-total"><span /><ImpactTotal v={v} /></div>
    <p className="foot">Click a metric for its explanation and the model assumptions behind it; click a cash-flow row for original-currency amounts, supporting assumptions and missing information.</p>
  </section>;
}

export function LiquidityPositionCard({ r, v }: { r: MarketRisk; v: RiskView }) {
  const { f, lq, rate } = v;
  const itemsUsd = lq.items_usd_m as Record<string, number>;
  const itemsTotal = Object.values(itemsUsd).reduce((a, b) => a + b, 0);
  const labels: Record<string, string> = { cash_and_equivalents: "Cash & cash equivalents", financial_assets: "Financial assets", credit_lines: "Credit lines" };
  const shades = ["#1b2a52", "#5b87d6", "#c9dcf6"];
  const t0 = lq.cash_t0;   // engine cash at T0, rolled forward from the last disclosure
  const minimumEur = t0?.reserve_eur_m ?? lq.minimum_eur_m;
  return <section className="card" aria-labelledby="lp">
    <h2 id="lp">Liquidity Position</h2><p className="sub">As of {when(lq.balance_date, false)} (EUR million)</p>
    <div className="inner">
      <div className="small">Last reported ({when(lq.balance_date, false)})</div><div className="big">{num(lq.current_eur_m, 0)}</div>
      <div className="stack" role="img" aria-label="Liquidity composition">{Object.keys(itemsUsd).map((k, i) =>
        <i key={k} data-tip={`${labels[k] ?? k}\n€${Math.round(itemsUsd[k] / rate).toLocaleString("en-US")}m (USD ${itemsUsd[k].toLocaleString("en-US")}m)\n${lq.provenance}: ${lq.source}`} style={{ width: `${itemsUsd[k] / itemsTotal * 100}%`, background: shades[i] }} />)}</div>
      <table className="legend"><tbody>{Object.keys(itemsUsd).map((k, i) => (
        <tr key={k}><td><i style={{ background: shades[i] }} />{labels[k] ?? k}</td><td className="r">{(itemsUsd[k] / itemsTotal * 100).toFixed(1)}%</td><td className="r">{Math.round(itemsUsd[k] / rate).toLocaleString("en-US")}</td></tr>))}</tbody></table>
      {t0 && <div className="t0line" data-tip={`Engine cash at T0 ${when(f.as_of, false)}, rolled forward from the ${when(lq.balance_date, false)} disclosure (operating cash flow, interest, lease and capex since then; ${t0.label}).\nCash at T0 ${num(t0.eur_m, 0)}\n+ undrawn credit lines ${num(t0.credit_lines_eur_m, 0)}${t0.special_fund_eur_m ? `\n+ special fund ${num(t0.special_fund_eur_m, 0)}` : ""}\n${MINUS} minimum reserve ${num(t0.reserve_eur_m, 0)}\n= headroom at T0 ${num(t0.headroom_t0_eur_m, 0)}\nUSD ${num(t0.usd_m, 0)}m at the T0 rate ${rate}.`}>
        <span>Cash at T0 (rolled forward)</span><b>{num(t0.eur_m, 0)}</b></div>}
    </div>
    <div className={`inner shade ${r.custom ? "scen" : ""}`}>
      <div className="small">Scenario-based liquidity reserve</div><div className="big">{num(lq.scenario_eur_m, 0)}</div>
      <div className="small muted">After {r.custom ? "the what-if" : "estimated"} {r.months}-month market impacts</div>
      <div className={`headroom ${lq.scenario_headroom_eur_m < 0 ? "neg" : ""}`}><span>Headroom above minimum ({num(minimumEur, 0)})</span><b>{num(lq.scenario_headroom_eur_m, 0)}</b></div>
    </div>
    <p className="foot">Reported in USD {num(lq.reported_usd_m, 0)}m, displayed at the {when(f.as_of, false)} T0 USD/EUR rate {rate}. {lq.minimum_note}</p>
  </section>;
}

export function DebtMaturityCard({ v }: { v: RiskView }) {
  const f = v.f;
  return <section className="card" aria-labelledby="dm">
    <h2 id="dm">Debt Maturity Profile</h2><p className="sub">Financial debt and lease liabilities (EUR million) <Prov p={f.debt_maturity.provenance} /></p>
    {f.debt_maturity.available !== false ? <DebtChart debt={f.debt_maturity} /> : <p className="small muted">{f.debt_maturity.note ?? "A verified debt maturity schedule is not available."}</p>}
    <div className="dlegend">{f.debt_maturity.categories.map((c: string, i: number) => <span key={c}><i style={{ background: debtColor(i) }} />{c}</span>)}</div>
    {f.insights.debt.available && <div className={`debtcall ${f.insights.debt.flag ?? "ok"}`} data-tip={f.insights.debt.assumption}>
      <div><b>{f.insights.debt.peak_year} refinancing peak:</b> {f.insights.debt.peak_amount.toLocaleString("en-US")} due.</div>
      <div>{f.insights.debt.cover_text}</div></div>}
    <p className="foot">{f.debt_maturity.reconciliation_note}</p>
  </section>;
}

export function WaterfallCard({ v }: { v: RiskView }) {
  const w = v.f.insights.waterfall;
  return <section className="card" aria-labelledby="wf">
    <h2 id="wf">Liquidity waterfall</h2><p className="sub">Reported reserve to scenario reserve, by driver (EUR million)</p>
    {w.available !== false ? <Waterfall w={w} /> : <p className="small muted">{w.note ?? "The liquidity waterfall is unavailable for this input set."}</p>}
    {w.upside_not_counted.length > 0 && <p className="foot">Not counted (favourable): {w.upside_not_counted.map((u: any) => `${ROW_LABEL[u.id]} ${eur(u.eur_m)}`).join(", ")}. {w.note}</p>}
  </section>;
}

export function MonitoringCard({ r, v }: { r: MarketRisk; v: RiskView }) {
  return <section className="card" aria-labelledby="be">
    <h2 id="be">How close to trouble?</h2><p className="sub">Current scenario monitoring · Next {r.months} months · As of {when(v.f.as_of, false)}</p>
    <Monitoring liquidity={v.lq} calculation={v.f.calculation ?? v.f.cash_engine} />
  </section>;
}
