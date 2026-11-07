import { useRef, useState, type ReactNode } from "react";
import { MINUS } from "./format";
import { wholeLabel } from "./engineUi";

// Hover position as a 0..1 ratio of the element's width: independent of page zoom, so tooltips stay aligned when the UI is scaled.
function useHover(n: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [idx, setIdx] = useState<number | null>(null);
  const onMove = (e: React.MouseEvent) => {
    const r = ref.current?.getBoundingClientRect(); if (!r || r.width === 0 || n === 0) return;
    const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    setIdx(Math.round(ratio * (n - 1)));
  };
  return { ref, idx, onMove, onLeave: () => setIdx(null) };
}

function Tip({ left, children, below }: { left: number; children: ReactNode; below?: boolean }) {
  const shift = left < 25 ? "0%" : left > 75 ? "-100%" : "-50%";
  return <div className={`ctip ${below ? "below" : ""}`} style={{ left: `${left}%`, transform: `translateX(${shift})` }} role="tooltip">{children}</div>;
}

export function Sparkline({ points, tone, fmt, source, width = 84, height = 30 }:
  { points: { date: string; value: number }[]; tone: string | null; fmt: (v: number) => string; source?: string; width?: number; height?: number }) {
  const h = useHover(points.length);
  const values = points.map(p => p.value);
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const X = (i: number) => i / (values.length - 1) * (width - 2) + 1, Y = (v: number) => height - 3 - ((v - min) / span) * (height - 6);
  const pts = values.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const color = tone === "adverse" ? "var(--red)" : tone === "favourable" ? "var(--green)" : "var(--muted)";
  return (
    <div className="spark" ref={h.ref} onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-label={`3-month trend, ${points[0].date} to ${points[points.length - 1].date}`} role="img">
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
        {h.idx != null && <><line x1={X(h.idx)} x2={X(h.idx)} y1={0} y2={height} stroke="var(--muted)" strokeWidth=".6" /><circle cx={X(h.idx)} cy={Y(values[h.idx])} r="2.4" fill={color} /></>}
      </svg>
      {h.idx != null && <Tip left={X(h.idx) / width * 100}><b>{fmt(values[h.idx])}</b><span>{points[h.idx].date}</span>{source && <span className="src">{source}</span>}</Tip>}
    </div>
  );
}

export function LineChart({ points, unit }: { points: { date: string; value: number }[]; unit: string }) {
  const W = 420, H = 150, P = { l: 42, r: 8, t: 8, b: 20 };
  const h = useHover(points.length);
  if (points.length < 2) return <p className="muted">No history available.</p>;
  const vs = points.map(p => p.value), min = Math.min(...vs), max = Math.max(...vs), span = max - min || 1;
  const x = (i: number) => P.l + (i / (points.length - 1)) * (W - P.l - P.r);
  const y = (v: number) => P.t + (1 - (v - min) / span) * (H - P.t - P.b);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const ticks = [min, (min + max) / 2, max];
  return (
    <div className="spark block" ref={h.ref} onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
      <svg viewBox={`0 0 ${W} ${H}`} className="linechart" role="img" aria-label={`History in ${unit}, ${points[0].date} to ${points[points.length - 1].date}`}>
        {ticks.map((t, i) => <g key={i}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" /><text x={P.l - 5} y={y(t) + 3} textAnchor="end" className="axis">{Math.abs(t) >= 100 ? t.toFixed(0) : t.toFixed(3)}</text></g>)}
        <path d={d} fill="none" stroke="var(--navy)" strokeWidth="1.8" />
        {h.idx != null && <><line x1={x(h.idx)} x2={x(h.idx)} y1={P.t} y2={H - P.b} stroke="var(--muted)" strokeWidth=".7" /><circle cx={x(h.idx)} cy={y(vs[h.idx])} r="3" fill="var(--navy)" /></>}
        <text x={P.l} y={H - 5} className="axis">{points[0].date}</text>
        <text x={W - P.r} y={H - 5} textAnchor="end" className="axis">{points[points.length - 1].date}</text>
      </svg>
      {h.idx != null && <Tip left={x(h.idx) / W * 100}><b>{vs[h.idx]} {unit}</b><span>{points[h.idx].date}</span></Tip>}
    </div>
  );
}

export function SignedBar({ value, max, tip }: { value: number | null; max: number; tip?: string }) {
  // centred zero line; red to the left for adverse, green to the right for favourable
  const w = value == null || max === 0 ? 0 : Math.min(50, Math.abs(value) / max * 50);
  return (
    <div className="sbar" data-tip={tip}>
      <div className="sbar-track" />
      {value != null && value !== 0 && <div className={`sbar-fill ${value < 0 ? "neg" : "pos"}`} style={value < 0 ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }} />}
      <div className="sbar-zero" />
    </div>
  );
}

const DEBT_COLORS = ["#1b2a52", "#5b87d6", "#8fb3ec", "#c9dcf6"];
export const debtColor = (i: number) => DEBT_COLORS[i % DEBT_COLORS.length];

export function DebtChart({ debt }: { debt: any }) {
  const years: string[] = debt.years, cats: string[] = debt.categories;
  const h = useHover(years.length);
  const totals = years.map(y => debt.totals[y] as number);
  const top = Math.max(...totals), ymax = Math.ceil(top / 500) * 500 || 500;
  const W = 330, H = 150, P = { l: 30, r: 4, t: 14, b: 16 };
  const bw = (W - P.l - P.r) / years.length;
  const y = (v: number) => P.t + (1 - v / ymax) * (H - P.t - P.b);
  const onMove = (e: React.MouseEvent) => {           // map to bars only (ignore the axis gutter)
    const r = h.ref.current?.getBoundingClientRect(); if (!r) return;
    const xs = (e.clientX - r.left) / r.width * W;
    const i = Math.floor((xs - P.l) / bw);
    h.onMove({ ...e, clientX: r.left + (i < 0 ? 0 : i >= years.length ? 1 : i / Math.max(1, years.length - 1)) * r.width } as any);
  };
  const hi = h.idx;
  return (
    <div className="spark block" ref={h.ref} onMouseMove={onMove} onMouseLeave={h.onLeave}>
      <svg viewBox={`0 0 ${W} ${H}`} className="debtchart" role="img" aria-label={`Debt maturity by year, ${debt.unit}`}>
        {[0, ymax / 2, ymax].map(t => <g key={t}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" /><text x={P.l - 4} y={y(t) + 3} textAnchor="end" className="axis">{t.toLocaleString("en-US")}</text></g>)}
        {years.map((yr, i) => {
          let acc = 0; const x0 = P.l + i * bw + bw * 0.18, w = bw * 0.64;
          return (
            <g key={yr} opacity={hi == null || hi === i ? 1 : 0.45}>
              {cats.map((c, k) => { const v = debt.values[yr][k] as number; const y1 = y(acc + v), hh = y(acc) - y1; acc += v;
                return <rect key={c} x={x0} y={y1} width={w} height={Math.max(0, hh)} fill={debtColor(k)} />; })}
              <text x={x0 + w / 2} y={y(acc) - 3} textAnchor="middle" className="bartotal">{wholeLabel(acc)}</text>
              <text x={x0 + w / 2} y={H - 3} textAnchor="middle" className="axis">{yr}</text>
            </g>
          );
        })}
      </svg>
      {hi != null && <Tip left={(P.l + hi * bw + bw / 2) / W * 100} below>
        <b>{years[hi]}: {wholeLabel(totals[hi])} {debt.unit}</b>
        {cats.map((c, k) => <span key={c} className="trow"><i style={{ background: debtColor(k) }} />{c}<em>{wholeLabel(debt.values[years[hi]][k] as number)}</em></span>)}
        <span className="src">{debt.provenance}: estimated from the supplied layout, not reported</span>
      </Tip>}
    </div>
  );
}
/** Baseline and scenario liquidity paths on one chart with the minimum reserve line (values already in display units). */
// `timed` places points by calendar date (for unevenly spaced paths such as daily-then-month-end); `marker` draws one labelled date line.
export function PathChart({ baseline, scenario, reserve, unit, timed = false, marker }: { baseline: { date: string; value: number }[]; scenario: { date: string; value: number }[]; reserve: number; unit: string; timed?: boolean; marker?: { date: string; label: string } }) {
  const W = 640, H = 200, P = { l: 52, r: 10, t: 12, b: 22 };
  const n = Math.max(baseline.length, scenario.length);
  const h = useHover(n);
  if (n < 2) return <p className="muted small">No liquidity path for this run.</p>;
  const all = [...baseline, ...scenario].map(p => p.value).concat(reserve);
  const lo0 = Math.min(...all), hi0 = Math.max(...all), pad = (hi0 - lo0) * 0.06 || 1;
  const lo = Math.max(0, lo0 - pad), hi = hi0 + pad;
  const dates = (baseline.length >= scenario.length ? baseline : scenario).map(p => p.date);
  const t = (d: string) => Date.parse(d + "T00:00:00Z"), t0 = t(dates[0]), span = Math.max(t(dates[n - 1]) - t0, 1);
  const xr = (r: number) => P.l + r * (W - P.l - P.r);
  const xd = (d: string) => xr((t(d) - t0) / span);
  const x = (i: number) => timed ? xd(dates[i]) : xr(i / (n - 1));
  const y = (v: number) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const line = (pts: { value: number }[]) => pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const ticks = [lo, (lo + hi) / 2, hi];
  // With date spacing the hover ratio is matched to the nearest dated point.
  const nearest = (r: number) => dates.reduce((best, d, k) => Math.abs(xd(d) - xr(r)) < Math.abs(xd(dates[best]) - xr(r)) ? k : best, 0);
  const i = h.idx == null ? null : timed ? nearest(h.idx / (n - 1)) : h.idx;
  const markerX = marker && marker.date >= dates[0] && marker.date <= dates[n - 1] ? xd(marker.date) : null;
  return (
    <div className="spark block" ref={h.ref} onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
      <svg viewBox={`0 0 ${W} ${H}`} className="pathchart" role="img" aria-label={`Baseline and scenario liquidity paths with the minimum reserve, ${unit}`}>
        {ticks.map((t, k) => <g key={k}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" /><text x={P.l - 5} y={y(t) + 3} textAnchor="end" className="axis">{Math.round(t).toLocaleString("en-US")}</text></g>)}
        <line x1={P.l} x2={W - P.r} y1={y(reserve)} y2={y(reserve)} className="minline" />
        <text x={W - P.r} y={y(reserve) - 4} textAnchor="end" className="minlabel">Minimum reserve {Math.round(reserve).toLocaleString("en-US")}</text>
        {markerX != null && <g><line x1={markerX} x2={markerX} y1={P.t} y2={H - P.b} stroke="var(--muted)" strokeWidth=".8" strokeDasharray="2 3" /><text x={markerX + 4} y={P.t + 9} className="axis">{marker!.label}</text></g>}
        <path d={line(baseline)} fill="none" className="path-base" />
        <path d={line(scenario)} fill="none" className="path-scen" />
        {i != null && <line x1={x(i)} x2={x(i)} y1={P.t} y2={H - P.b} stroke="var(--muted)" strokeWidth=".7" />}
        <text x={P.l} y={H - 5} className="axis">{dates[0]}</text>
        <text x={W - P.r} y={H - 5} textAnchor="end" className="axis">{dates[dates.length - 1]}</text>
      </svg>
      {i != null && <Tip left={x(i) / W * 100}><b>{dates[i]}</b>
        {baseline[i] && <span>Baseline {Math.round(baseline[i].value).toLocaleString("en-US")}</span>}
        {scenario[i] && <span>Scenario {Math.round(scenario[i].value).toLocaleString("en-US")}</span>}
        {scenario[i] && <span className="src">Scenario headroom {Math.round(scenario[i].value - reserve).toLocaleString("en-US")} {unit}</span>}</Tip>}
    </div>
  );
}
export { MINUS };
