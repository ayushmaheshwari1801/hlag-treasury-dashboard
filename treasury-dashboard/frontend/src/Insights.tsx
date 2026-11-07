import { eur, num, ROW_LABEL, when } from "./format";

const toneIcon: Record<string, string> = { move: "\u2195", impact: "\u25BC", headroom: "\u25A3", breakeven: "\u26A0" };

export function WhatChanged({ bullets, onPick, title = "What changed" }: { bullets: any[]; onPick: (id: string) => void; title?: string }) {
  if (!bullets.length) return null;
  return (
    <section className="card changed" aria-label={title}>
      {title && <h2>{title}</h2>}
      <ul>{bullets.map((b, i) => (
        <li key={i} className={`wc-${b.tone ?? "neutral"}`}>
          <span className="wc-ic" aria-hidden>{toneIcon[b.kind] ?? "\u2022"}</span>
          <button className="link plain-text" onClick={() => onPick(b.id)} title="Highlight related items">{b.text}</button>
        </li>))}</ul>
    </section>
  );
}

export function Waterfall({ w }: { w: any }) {
  const steps = w.steps as any[];
  const W = 520, H = 190, P = { l: 46, r: 10, t: 18, b: 34 };
  const start = steps[0].eur_m as number;
  const ymin = Math.max(0, Math.floor((Math.min(w.minimum_eur_m, steps[steps.length - 1].eur_m) * 0.85) / 500) * 500);
  const ymax = Math.ceil((start * 1.02) / 100) * 100;
  const y = (v: number) => P.t + (1 - (v - ymin) / (ymax - ymin)) * (H - P.t - P.b);
  const bw = (W - P.l - P.r) / steps.length;
  let level = start;
  const ticks = [ymin, (ymin + ymax) / 2, ymax];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="waterfall" role="img" aria-label={`Liquidity waterfall from ${num(start, 0)} to ${num(steps[steps.length - 1].eur_m, 0)} EUR million`}>
      {ticks.map(t => <g key={t}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" /><text x={P.l - 5} y={y(t) + 3} textAnchor="end" className="axis">{Math.round(t).toLocaleString("en-US")}</text></g>)}
      <line x1={P.l} x2={W - P.r} y1={y(w.minimum_eur_m)} y2={y(w.minimum_eur_m)} className="minline" />
      {steps.map((s, i) => {
        const x = P.l + i * bw + bw * 0.16, wd = bw * 0.68;
        let top: number, bot: number, cls: string;
        if (s.kind === "start" || s.kind === "end") { top = y(s.eur_m); bot = y(ymin); cls = s.kind; level = s.eur_m; }
        else { top = y(level); bot = y(level + s.eur_m); cls = "down"; level += s.eur_m; }
        return (
          <g key={i} data-tip>
            <rect x={x} y={Math.min(top, bot)} width={wd} height={Math.max(1.5, Math.abs(bot - top))} className={`wf-${cls}`}><title>{`${s.label}: ${s.kind === "down" ? eur(s.eur_m) : num(s.eur_m, 0)}`}</title></rect>
            <text x={x + wd / 2} y={Math.min(top, bot) - 4} textAnchor="middle" className="bartotal">{s.kind === "down" ? num(s.eur_m, 1) : num(s.eur_m, 0)}</text>
            <text x={x + wd / 2} y={H - 18} textAnchor="middle" className="axis">{(s.id ? ROW_LABEL[s.id] : s.label).split(" ")[0]}</text>
            <text x={x + wd / 2} y={H - 8} textAnchor="middle" className="axis">{(s.id ? ROW_LABEL[s.id] : s.label).split(" ").slice(1).join(" ")}</text>
          </g>);
      })}
      <text x={(W + P.l) / 2} y={y(w.minimum_eur_m) - 4} textAnchor="middle" className="minlabel">Minimum liquidity {num(w.minimum_eur_m, 0)}</text>
      <text x={W - P.r} y={10} textAnchor="end" className="axis">Axis starts at {ymin.toLocaleString("en-US")} so the steps are visible</text>
    </svg>
  );
}

export function Breakevens({ list, onPick }: { list: any[]; onPick: (id: string) => void }) {
  return (
    <ul className="be">{list.map(b => (
      <li key={b.id} className={b.within_range ? "near" : "far"}>
        <button className="link plain-text" onClick={() => onPick(b.id)}>
          <span className={`chip ${b.within_range ? "bad" : "ok"}`}>{b.within_range ? "within range" : "remote"}</span> {b.text}
        </button>
      </li>))}</ul>
  );
}


const finite = (value: any): value is number => typeof value === "number" && Number.isFinite(value);
const STATUS_LABEL: Record<string, string> = { LOW: "Low", WATCH: "Watch", HIGH: "High", CRITICAL: "Critical", INCOMPLETE: "Incomplete", UNAVAILABLE: "Unavailable" };

/** The scenario's liquidity risk status as shown in monitoring (and the context bar): the recorded status, or
 *  INCOMPLETE when required inputs are missing and the recorded status is not already HIGH or CRITICAL. */
export function riskStatus(liquidity: any, calculation: any) {
  const lq = liquidity ?? {}, calc = calculation ?? {};
  const missing = Boolean(calc.flags?.incomplete_data || calc.missing_inputs?.length);
  const incomplete = calc.monitoring_complete === false || missing || !finite(lq.scenario_eur_m) || !finite(lq.scenario_headroom_eur_m)
    || !finite(lq.minimum_eur_m) || !lq.scenario_min_date;
  const recordedStatus = String(calc.status ?? "UNAVAILABLE").toUpperCase();
  const status = incomplete && !["CRITICAL", "HIGH"].includes(recordedStatus) ? "INCOMPLETE" : recordedStatus;
  const tone = status === "CRITICAL" ? "bad" : status === "LOW" ? "ok" : "warn";
  const firstBreach = calc.first_breach_date ? when(calc.first_breach_date, false)
    : calc.first_breach_date === null && !incomplete ? "None in selected horizon" : "Unavailable";
  return { status, tone, label: STATUS_LABEL[status] ?? status, incomplete, firstBreach };
}

export function Monitoring({ liquidity, calculation }: { liquidity: any; calculation: any }) {
  const lq = liquidity ?? {}, calc = calculation ?? {};
  const amount = (value: any) => finite(value) ? `EUR ${num(value, 1)}m` : "Unavailable";
  const { tone, label, incomplete, firstBreach } = riskStatus(lq, calc);
  const consumed = calc.consumed_pct;
  const minimumDate = lq.scenario_min_date ? when(lq.scenario_min_date, false) : "Unavailable";
  const days = finite(calc.days_until_minimum) ? ` (in ${calc.days_until_minimum} days)` : "";
  const values = [
    ["Minimum projected liquidity", amount(lq.scenario_eur_m)],
    ["Headroom above operating minimum", amount(lq.scenario_headroom_eur_m)],
    ["Minimum operating reserve", amount(lq.minimum_eur_m)],
    ["Minimum-liquidity date", minimumDate + days],
    [finite(consumed) && consumed < 0 ? "Headroom gained vs baseline" : "Headroom used vs baseline", finite(consumed) ? `${num(Math.abs(consumed), 1)}%` : "Unavailable"],
    ["First projected reserve breach", firstBreach],
  ];
  return <div className="monitoring" aria-label="Current scenario liquidity monitoring">
    <p className="small">Risk status <span className={`chip ${tone}`}>{label}</span></p>
    <table className="legend" aria-label="Liquidity monitoring measures"><tbody>
      {values.map(([label, value]) => <tr key={label}>
        <th scope="row" className="muted" style={{ textAlign: "left", fontWeight: 400, padding: "4px 0" }}>{label}</th>
        <td style={{ textAlign: "right", fontWeight: 600, paddingLeft: 12 }}>{value}</td>
      </tr>)}
    </tbody></table>
    {incomplete && <p className="small muted">Required inputs are incomplete; monitoring values are partial estimates.</p>}
    {calc.horizon_end && <p className="small muted">Selected scenario through {when(calc.horizon_end, false)}.</p>}
  </div>;
}
