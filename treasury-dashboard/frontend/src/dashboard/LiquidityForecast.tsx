// Step 1: the liquidity forecast in the place of the uploaded frontend's cash chart. Lines: the baseline, the
// scenario selected in the What-if panel, and one line per News Event run on its own (see useEventImpact). Every
// point is a saved run's liquidity path (cash + undrawn credit lines), converted to EUR at the analysis-date exchange rate for display.
import { useEffect, useState, type MouseEvent } from "react";
import { api } from "../api";
import { RunStatus } from "../EnginePanels";
import { eur, num, when } from "../format";
import { liquidityPath, type PathPoint } from "./useEventImpact";
import type { MarketRisk } from "./useMarketRisk";
import type { RiskView } from "./MarketCards";

export const LINE_COLORS = ["#d1495b", "#2e86ab", "#edae49", "#00798c", "#8e5ea2", "#66a182", "#e07a5f", "#3d405b", "#f2a541", "#577590"];
export interface LiquidityLine { id: string; label: string; color: string; points: PathPoint[]; dashed?: boolean }

/** The baseline and selected-scenario liquidity paths of the dashboard run. null: the engine provides none. */
export function useRunPaths(runId: string | undefined, rate: number | undefined, horizonEnd: string | undefined) {
  const [state, setState] = useState<{ runId?: string; baseline: PathPoint[]; scenario: PathPoint[]; reserve: number | null; error: string } | null>(null);
  useEffect(() => {
    if (!runId || !rate) return;
    let live = true;
    api.engineCashflows(runId).then(c => {
      if (!live) return;
      const l = c.liquidity;
      setState(l ? { runId, baseline: liquidityPath(l.baseline_path, rate, horizonEnd), scenario: liquidityPath(l.scenario_path, rate, horizonEnd), reserve: l.reserve_usd_m / rate, error: "" }
        : { runId, baseline: [], scenario: [], reserve: null, error: "This run has no liquidity path." });
    }).catch(e => { if (live) setState({ runId, baseline: [], scenario: [], reserve: null, error: e.message }); });
    return () => { live = false; };
  }, [runId, rate, horizonEnd]);
  return state;
}

export function LiquidityForecastCard({ r, v, paths, selectedLabel, lines, onOpenDetail, onOpenLine }:
  { r: MarketRisk; v: RiskView; paths: ReturnType<typeof useRunPaths>; selectedLabel: string; lines: LiquidityLine[]; onOpenDetail: () => void; onOpenLine: (id: string) => void }) {
  const f = v.f, calc = f.calculation;
  const ready = paths && paths.baseline.length > 1;
  return <section className="card liquidity-forecast" aria-labelledby="lf-title">
    <div className="hd"><div><h2 id="lf-title">Liquidity Forecast</h2>
      <p className="sub">Baseline, the selected scenario and each News Event on its own · {when(f.as_of, false)} to {when(calc?.horizon_end, false)} · EUR million</p></div>
      {calc?.run_id && <div className="lf-actions"><button className="preset" onClick={onOpenDetail}>Cash-flow detail</button></div>}</div>
    <RunStatus calc={calc} rate={v.rate} months={r.months} rows={v.ci.rows} />
    {!paths && calc?.run_id && <p className="muted small" role="status">Loading the liquidity path…</p>}
    {paths && !ready && <p className="lf-note small">The liquidity forecast is unavailable for this assessment. The figures on the rest of the page still apply.</p>}
    {ready && <LiquidityLinesChart baseline={paths!.baseline} reserve={paths!.reserve} lines={[{ id: "__selected__", label: `Selected scenario · ${selectedLabel}`, color: "var(--navy)", points: paths!.scenario, dashed: true }, ...lines]}
      onOpenLine={id => (id === "__selected__" ? onOpenDetail() : onOpenLine(id))} onSelect={onOpenDetail} />}
    <p className="foot">Liquidity = cash + undrawn credit lines (+ special fund when counted); the gap to the reserve line is headroom. Original amounts are in USD, shown in EUR at the analysis-date exchange rate (USD {v.rate} per EUR). News Event lines are separate what-if runs and do not add up.</p>
  </section>;
}

/** Baseline, the selected scenario and one line per News Event, as liquidity or as the change against the baseline. */
export function LiquidityLinesChart({ baseline, reserve, lines, onOpenLine, onSelect, compact = false }:
  { baseline: PathPoint[]; reserve: number | null; lines: LiquidityLine[]; onOpenLine?: (id: string) => void; onSelect?: () => void; compact?: boolean }) {
  // News impacts are millions against a multi-billion balance, so the default view is the change vs baseline.
  const [mode, setMode] = useState<"delta" | "level">("delta");
  const [hover, setHover] = useState<number | null>(null), [focus, setFocus] = useState<string | null>(null);
  const delta = mode === "delta";
  const dates = baseline.map(p => p.date), baseAt = new Map(baseline.map(p => [p.date, p.value]));
  const series = lines.filter(l => l.points.length > 1).map(l => ({ ...l, at: new Map(l.points.map(p => [p.date, p.value])) }));
  const valueOf = (at: Map<string, number>, d: string) => { const x = at.get(d), b = baseAt.get(d); return x == null ? undefined : delta ? (b == null ? undefined : x - b) : x; };
  const values = [...(delta ? [0] : [...baseline.map(p => p.value), ...(reserve != null ? [reserve] : [])]),
    ...series.flatMap(s => dates.map(d => valueOf(s.at, d)))].filter((x): x is number => x != null && Number.isFinite(x));
  // compact: a narrower drawing for half-width boxes, so the axis text keeps its size when scaled down.
  const width = compact ? 470 : 850, height = compact ? 230 : 280, pad = { left: 70, right: 18, top: 16, bottom: 28 };
  const lo = Math.min(...values), hi = Math.max(...values), spread = Math.max(hi - lo, delta ? 1 : 10);
  const min = lo - spread * .08, max = hi + spread * .08;
  const t = (d: string) => Date.parse(d + "T00:00:00Z"), t0 = t(dates[0]), span = Math.max(t(dates[dates.length - 1]) - t0, 1);
  const x = (d: string) => pad.left + (t(d) - t0) / span * (width - pad.left - pad.right);
  const y = (val: number) => pad.top + (max - val) / (max - min) * (height - pad.top - pad.bottom);
  const path = (get: (d: string) => number | undefined) => dates.map(d => [d, get(d)] as const).filter(([, val]) => val != null && Number.isFinite(val))
    .map(([d, val], i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(val!).toFixed(1)}`).join(" ");
  const nearest = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect(), sx = (e.clientX - box.left) / box.width * width;
    return dates.reduce((best, d, i) => Math.abs(x(d) - sx) < Math.abs(x(dates[best]) - sx) ? i : best, 0);
  };
  const day = hover == null ? null : dates[hover];
  const ticks = [max - spread * .08, (max + min) / 2, min + spread * .08];
  const tickText = (val: number) => delta ? eur(val, Math.abs(spread) < 20 ? 1 : 0) : `€${num(val, 0)}m`;
  const fmt = (val: number | undefined) => val == null ? "—" : delta ? eur(val) : `€${num(val, 0)}m`;
  const tone = (val: number | undefined) => val == null || !delta ? "" : val < -0.05 ? "neg" : val > 0.05 ? "pos" : "";
  const last = dates[dates.length - 1];
  return <div className="scenario-lines">
    <div className="scenario-lines-head">
      <div className="presets" role="tablist" aria-label="Chart values">
        <button role="tab" aria-selected={delta} className={`preset ${delta ? "on" : ""}`} onClick={() => setMode("delta")}>Change vs baseline</button>
        <button role="tab" aria-selected={!delta} className={`preset ${!delta ? "on" : ""}`} onClick={() => setMode("level")}>Liquidity</button>
      </div>
      {!compact && <small>Each coloured line: {delta ? "how much more or less liquidity than the baseline" : "the liquidity"} if only that News Event happened. Click a line's name for its detail; click the chart for the cash-flow detail.</small>}
    </div>
    <div className={`scenario-lines-area${onSelect ? "" : " static"}`}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Liquidity forecast: baseline, selected scenario and each News Event scenario"
        onMouseMove={e => setHover(nearest(e))} onMouseLeave={() => setHover(null)} onClick={() => onSelect?.()}>
        {ticks.map((val, i) => <g key={i}><line x1={pad.left} x2={width - pad.right} y1={y(val)} y2={y(val)} className="grid" />
          <text x={pad.left - 6} y={y(val) + 3} textAnchor="end" className="axis">{tickText(val)}</text></g>)}
        {!delta && reserve != null && <line x1={pad.left} x2={width - pad.right} y1={y(reserve)} y2={y(reserve)} className="reserve" />}
        {delta ? <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} className="baseline" />
          : <path d={path(d => baseAt.get(d))} className="baseline" />}
        {series.map(s => <path key={s.id} d={path(d => valueOf(s.at, d))} fill="none" style={{ stroke: s.color }}
          strokeWidth={focus === s.id ? 3.4 : s.dashed ? 2 : 2.2} strokeDasharray={s.dashed ? "6 4" : undefined} opacity={focus && focus !== s.id ? .25 : 1} />)}
        {day && <line x1={x(day)} x2={x(day)} y1={pad.top} y2={height - pad.bottom} className="cursor" />}
        <text x={pad.left} y={height - 8} className="axis">{when(dates[0], false)}</text><text x={width - pad.right} y={height - 8} textAnchor="end" className="axis">{when(last, false)}</text>
      </svg>
      {day && <div className="scenario-lines-tip" style={{ left: `${Math.min(x(day) / width * 100, 60)}%` }}><b>{when(day, false)}</b>
        {!delta && <span><i className="key-baseline" />Baseline: {fmt(baseAt.get(day))}</span>}
        {series.map(s => <span key={s.id}><i style={{ background: s.color }} />{s.label}: <b className={tone(valueOf(s.at, day))}>{fmt(valueOf(s.at, day))}</b></span>)}</div>}
    </div>
    <div className="scenario-lines-legend">
      <span><i className="key-baseline" />{delta ? "Baseline (0)" : "Baseline"}</span>{!delta && reserve != null && <span><i className="key-reserve" />Minimum reserve €{num(reserve, 0)}m</span>}
      {series.map(s => { const end = valueOf(s.at, last), change = s.at.get(last) != null && baseAt.get(last) != null ? s.at.get(last)! - baseAt.get(last)! : undefined;
        return <button key={s.id} className="link" onMouseEnter={() => setFocus(s.id)} onMouseLeave={() => setFocus(null)} onClick={() => onOpenLine?.(s.id)} title={`At ${when(last, false)}: ${fmt(end)}`}>
          <i style={{ background: s.color }} className={s.dashed ? "dashed" : ""} />{s.label}{change != null && <b className={change < -0.05 ? "neg" : change > 0.05 ? "pos" : ""}> {eur(change)}</b>}</button>; })}
    </div>
  </div>;
}
