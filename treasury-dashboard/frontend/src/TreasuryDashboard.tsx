// Treasury Dashboard in the uploaded frontend's layout, on the current calculation engine. Read top to bottom:
//   sticky context bar → 1. liquidity forecast with the What-if panel beside it → 2. Market metrics ↔ cash-flow
//   impact with Working capital below it | News ↔ each Event's own cash impact, liquidity position below,
//   recommended actions on their own row.
// All figures come from the existing API (POST /api/v1/dashboard and the engine read endpoints); the browser only
// formats them. Panels of the uploaded frontend that need its newer Hybrid Forecast engine are not included.
import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { Drawer } from "./Drawer";
import { eur, num } from "./format";
import MetricDetail from "./MetricDetail";
import CashDetails from "./CashDetails";
import WhatIf from "./WhatIf";
import { CashFlows } from "./EnginePanels";
import { groupEnginePresets, presetFromOption } from "./enginePresets";
import { useMarketRisk } from "./dashboard/useMarketRisk";
import { useEventImpact } from "./dashboard/useEventImpact";
import { ContextBar, STEPS, scenarioLabel } from "./dashboard/ContextBar";
import { LiquidityPositionCard, MarketImpactCard, MonitoringCard, WaterfallCard, riskView } from "./dashboard/MarketCards";
import { NewsImpactCard, NewsImpactDetail, type NewsRow } from "./dashboard/EventImpactBoard";
import { mergePinned, usePinnedNews } from "./dashboard/pinnedNews";
import type { Granularity } from "./engineUi";

const GRANULARITY_KEY = "treasury.granularity.v1";
function savedGranularity(): Granularity {
  try { const g = localStorage.getItem(GRANULARITY_KEY); return g === "daily" || g === "weekly" ? g : "monthly"; } catch { return "monthly"; }
}
import { LINE_COLORS, LiquidityForecastCard, useRunPaths, type LiquidityLine } from "./dashboard/LiquidityForecast";
import { METRIC_EXPLAIN, modelAssumptions, newsDriversByMetric } from "./dashboard/metricAssumptions";
import { WorkingCapitalCard } from "./dashboard/WorkingCapital";
import { HedgeAgentCard } from "./dashboard/HedgeAgent";
import { ArticleDrawer, AssistantDrawer, NewsList, SourcesDrawer } from "./dashboard/RiskDrawers";
import "./TreasuryDashboard.css";

function Step({ id, children }: { id: typeof STEPS[number]["id"]; children: ReactNode }) {
  const index = STEPS.findIndex(s => s.id === id);
  return <section id={id} className="dash-section" aria-labelledby={`${id}-title`}>
    <header className="dash-section-head"><span className="dash-section-no" aria-hidden>{index + 1}</span><h2 id={`${id}-title`}>{STEPS[index].label}</h2></header>
    {children}
  </section>;
}

export default function TreasuryDashboard() {
  const r = useMarketRisk();
  const [modal, setModal] = useState<string | null>(null);                 // cash-flow row: native amounts and provenance
  const [metricDetail, setMetricDetail] = useState<string | null>(null);   // market metric: explanation and assumptions
  const [drawer, setDrawer] = useState<null | "news" | "brief" | "sources" | "cashflows">(null);
  const [article, setArticle] = useState<string | null>(null);
  const [impactDetail, setImpactDetail] = useState<NewsRow | null>(null);
  const [params, setParams] = useState<any[] | null>(null);               // engine assumptions; null = not available
  const [modelConfigured, setModelConfigured] = useState<boolean | null>(null);
  useEffect(() => { void api.engineAssumptions().then(d => setParams(d.parameters ?? [])).catch(() => setParams(null)); }, []);
  useEffect(() => { void api.assistantStatus().then(s => setModelConfigured(s?.mode === "model")).catch(() => setModelConfigured(false)); }, []);

  const pins = usePinnedNews();
  // Daily / Weekly / Monthly: one choice for the liquidity chart and the cash-flow table; remembered in this browser.
  const [granularity, setGranularityState] = useState<Granularity>(savedGranularity);
  const setGranularity = (g: Granularity) => { setGranularityState(g); try { localStorage.setItem(GRANULARITY_KEY, g); } catch { /* storage unavailable */ } };
  const v = r.data ? riskView(r) : null, f = v?.f;
  const rows: NewsRow[] = v ? mergePinned<NewsRow>(v.newsShown, pins.pinned) as NewsRow[] : [];
  const colors: Record<string, string> = Object.fromEntries(rows.map((row, i) => [row.event_id, LINE_COLORS[i % LINE_COLORS.length]]));
  const newsRunId = r.data ? r.data.news.run_id ?? r.data.news.export ?? "" : "";
  const impacts = useEventImpact({ eventIds: rows.map(row => row.event_id), asOf: f ? String(f.as_of).slice(0, 10) : undefined, horizonDays: f?.horizon_days,
    months: r.months, newsRunId, overrides: {}, modelConfigured, forecastCache: r.forecastCache, rate: v?.rate, horizonEnd: f?.calculation?.horizon_end });
  const paths = useRunPaths(f?.calculation?.run_id, v?.rate, f?.calculation?.horizon_end);
  const lines: LiquidityLine[] = rows.flatMap(row => { const item = impacts.items[row.event_id];
    return item?.status === "ready" && item.path?.length ? [{ id: row.event_id, label: row.title, color: colors[row.event_id], points: item.path }] : []; });
  const metricProps = (id: string) => ({ explain: METRIC_EXPLAIN[id], group: params ? modelAssumptions(id, params) : undefined, overrides: r.overrides, onOverride: r.setOverride,
    newsDrivers: newsDriversByMetric(rows, impacts.forecastFor, colors)[id] ?? [] });
  const asOf = f ? String(f.as_of).slice(0, 10) : new Date().toISOString().slice(0, 10);
  const pending = r.error && !r.data
    ? <p className="card err" role="alert">{r.error} <button className="link" onClick={() => void r.load()}>Retry</button></p>
    : <p className="card muted small" role="status">Loading the dashboard…</p>;

  return <div className="app treasury-dashboard">
    <header className="top">
      <div className="brand"><span className="mark" aria-hidden>{"◤"}</span><span className="word">Hapag-Lloyd</span></div>
      <div className="title"><h1>Treasury Decision Dashboard <span className="chip warn proto-tag" title="Synthetic, proxy and public inputs">Prototype</span></h1>
        <p>Key market developments, estimated cash-flow impacts and recommended actions.</p></div>
    </header>
    <ContextBar r={r} v={v} onSources={() => setDrawer("sources")} />
    {r.error && r.data && <p className="banner err" role="alert">{r.error}</p>}

    {/* 1 — Where we stand: the liquidity forecast (baseline, selected scenario, each News Event) and the What-if panel. */}
    <Step id="position">
      <div className="position-grid">
        <div className="position-main">
          {v && f ? <LiquidityForecastCard r={r} v={v} paths={paths} selectedLabel={scenarioLabel(r)} lines={lines} granularity={granularity} onGranularity={setGranularity}
            onOpenDetail={() => setDrawer("cashflows")} onOpenLine={id => { const row = rows.find(n => n.event_id === id); if (row) setImpactDetail(row); }} /> : pending}
          {v && <details className="card expandable" open><summary><b>What drives the liquidity path</b><small>Liquidity waterfall by driver · how close the scenario comes to the minimum reserve</small></summary>
            <div className="dash-grid two"><WaterfallCard v={v} /><MonitoringCard r={r} v={v} /></div></details>}
        </div>
        <aside id="market-sim" className="market-sim card" aria-label="What-if scenario">{!v || !f ? pending : <>
          <WhatIf scenario={f.scenario} cashImpacts={v.cashImpacts} shocks={r.shocks} preset={r.preset} busy={r.busy} open={r.wiOpen} setOpen={r.setWiOpen}
            onChange={r.setShock} onPreset={r.choosePreset} onReset={r.resetShocks}
            forecastActive={r.forecastActive} forecasting={r.forecasting} eventScenarios={[]} selectedEventId={r.selectedEventId}
            enginePresetGroups={groupEnginePresets(r.enginePresets)} activePresetId={r.activePreset?.id}
            onSelectEvent={id => { const chosen = presetFromOption(id, r.enginePresets);
              if (chosen) { r.setForecastActive(false); r.setSelectedEventId(""); r.setShocks({}); r.setPreset(id); r.setActivePreset(chosen); }
              else if (id === "__observed__") r.resetShocks();
              else if (id === "__manual__") { r.setActivePreset(null); r.setForecastActive(false); r.setSelectedEventId(""); r.setPreset("custom"); }
              }}
            deltas={[{ label: "Total impact", base: v.bf?.cash_impact.adverse_total_eur_m ?? null, now: v.ci.adverse_total_eur_m },
                     { label: "Scenario reserve", base: v.bf?.liquidity.scenario_eur_m ?? null, now: v.lq.scenario_eur_m },
                     { label: "Headroom", base: v.bf?.liquidity.scenario_headroom_eur_m ?? null, now: v.lq.scenario_headroom_eur_m }]} />
          <p className="stress-summary small">Next {r.months} months: adverse impact <b>{eur(v.ci.adverse_total_eur_m)}</b> · headroom above minimum <b>€{num(v.lq.scenario_headroom_eur_m, 0)}m</b>. Market and liquidity figures follow this scenario; news estimates are separate.</p>
        </>}</aside>
      </div>
    </Step>

    {/* 2 — Market data ↔ cash-flow impact | News ↔ Event impact (aligned rows), liquidity below; actions on their own row. */}
    <Step id="signals">
      <div className="board-grid">
        {/* Market data with Working capital (collections timing) directly below it. */}
        <div className="board-left">
          {v ? <MarketImpactCard r={r} v={v} onDetails={setMetricDetail} onCashDetails={setModal} /> : pending}
          {v && <WorkingCapitalCard r={r} v={v} params={params} />}
        </div>
        <div className="board-right">
          {v && <NewsImpactCard rows={rows} r={r} impacts={impacts} colors={colors} onSeeAll={() => setDrawer("news")} onUnpin={pins.unpin}
            onOpenNews={row => setArticle(row.article_ids[0] ?? row.id)} onOpenImpact={setImpactDetail} />}
          {v && <LiquidityPositionCard r={r} v={v} />}
        </div>
        {String(f?.calculation?.run_id ?? "").startsWith("spec_") && <div className="board-full"><HedgeAgentCard asOf={asOf} shocks={r.shocks} overrides={r.overrides} forecastInput={r.forecastInput} horizonDays={f?.horizon_days} rate={v?.rate} scenarioName={scenarioLabel(r)} scenarioKey={r.scenarioKey} newsRunId={newsRunId || undefined}
          eventIds={[...new Set([...(v?.scenarioEvents ?? []).map((e: any) => e.event_id), ...pins.pinned.map(p => p.event_id)])]} /></div>}
      </div>
    </Step>

    <button className="sparkle" onClick={() => setDrawer("brief")} aria-label="Open assistant" title="Assistant: briefing and questions">{"✦"} Ask</button>

    {modal && f?.calculation?.run_id && <CashDetails runId={f.calculation.run_id} rowId={modal} rowLabel={v?.ci.rows.find((x: any) => x.id === modal)?.label} horizonEnd={f.calculation.horizon_end} onClose={() => setModal(null)} />}
    {metricDetail && f && <MetricDetail rowId={metricDetail} finance={f} busy={r.busy} onClose={() => setMetricDetail(null)} onCashDetail={() => { setModal(metricDetail); setMetricDetail(null); }} {...metricProps(metricDetail)} />}
    {drawer === "cashflows" && f?.calculation?.run_id && <Drawer title="Cash-flow detail" modal wide onClose={() => setDrawer(null)}>
      <CashFlows runId={f.calculation.run_id} rate={v!.rate} horizonEnd={f.calculation.horizon_end} scenarioName={f.scenario?.full_scenario?.name} granularity={granularity} onGranularity={setGranularity} /></Drawer>}
    {drawer === "news" && <NewsList allowedEventIds={[]} onClose={() => setDrawer(null)} onOpen={setArticle} onForecast={() => {}}
      isPinned={pins.isPinned} onTogglePin={pins.toggle} onLoaded={(all, complete) => { if (complete) pins.reconcile(all); }} />}
    {drawer === "brief" && <AssistantDrawer months={r.months} shocks={r.shocks} overrides={r.overrides} forecastInput={r.forecastInput} onClose={() => setDrawer(null)} onOpenArticle={setArticle} />}
    {drawer === "sources" && <SourcesDrawer onClose={() => setDrawer(null)} reload={r.load} />}
    {article && <ArticleDrawer id={article} allowedEventIds={[]} onClose={() => setArticle(null)} onTest={t => { setArticle(null); r.testScenario(t); }} onForecast={() => {}} />}
    {impactDetail && v && <NewsImpactDetail row={impactDetail} item={impacts.items[impactDetail.event_id]} color={colors[impactDetail.event_id]} r={r} v={v} baseline={paths?.baseline ?? []} reserve={paths?.reserve ?? null}
      granularity={granularity} onGranularity={setGranularity}
      onClose={() => setImpactDetail(null)} onOpenArticle={setArticle} onApply={() => {}} />}
  </div>;
}
