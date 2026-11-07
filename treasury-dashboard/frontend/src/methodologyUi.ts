// Pure helpers for the row Methodology view. All content comes from the engine via /api/v1/engine/methodology;
// these only select, slice and format it.
import { assumptionText } from "./engineUi";

export const RANGE_DAYS: Record<string, number> = { "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "2Y": 730 };

/** Points inside the last `range` of the series (measured back from its last point). */
export function historySlice(points: { date: string; value: number }[], range: string) {
  if (!points.length || !RANGE_DAYS[range]) return points;
  const last = new Date(points[points.length - 1].date + "T00:00:00Z").getTime();
  const from = last - RANGE_DAYS[range] * 86400000;
  return points.filter(p => new Date(p.date + "T00:00:00Z").getTime() >= from);
}

const plain = (v: number) => Number(v.toPrecision(6)).toLocaleString("en-US", { maximumFractionDigits: 4 });

const title = (k: string) => k.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

/** A symbol's current value: one value, or one per trade / series / currency ("Asia Europe 1,367 · …"). Profile
 *  values use the Assumptions panel's formatting; market and anchor values are plain numbers with their unit. */
export function symbolValueText(sym: { value: unknown; unit?: string | null; key?: string | null; ref?: string }): string {
  const one = (v: unknown, key?: string | null): string => {
    if (v == null) return "—";
    if (typeof v === "object" && !Array.isArray(v)) {
      const entries = Object.entries(v as Record<string, unknown>);
      return entries.length ? entries.map(([k, x]) => `${title(k)} ${one(x, key?.replace(/\{\w+\}/, k))}`).join(" · ") : "none in this scenario";
    }
    if (sym.ref === "param") return assumptionText(v, sym.unit, key ?? undefined);
    if (typeof v === "number") return `${plain(v)}${sym.unit ? " " + sym.unit : ""}`;
    return String(v);
  };
  if (sym.value == null) return sym.ref === "none" ? "varies (see definition)" : sym.ref === "scenario" ? "not set in this scenario" : "—";
  return one(sym.value, sym.key);
}

/** Latest value of a header series, with its unit. */
export function seriesValueText(s: any): string {
  if (s?.value == null) return "—";
  const v = s.value as number;
  const d = String(s.series_id).startsWith("FX_") ? (v < 0.1 ? 5 : 4) : s.unit === "%" ? 2 : v >= 100 ? 0 : 2;
  const n = v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return s.unit === "%" ? `${n}%` : `${n} ${s.unit ?? ""}`.trim();
}

/** 1-week change: bp for rates, otherwise % (with the absolute move for prices). */
export function seriesChangeText(s: any): string {
  if (s?.change == null) return "—";
  const sign = (x: number) => (x > 0 ? "+" : x < 0 ? "−" : "");
  if (s.change_unit === "bp") return `${sign(s.change)}${Math.abs(s.change).toFixed(1)} bp`;
  const pct = s.change_pct == null ? "" : `${sign(s.change_pct)}${Math.abs(s.change_pct).toFixed(2)}%`;
  const abs = String(s.series_id).startsWith("FX_") ? "" : ` (${sign(s.change)}${Math.abs(s.change).toLocaleString("en-US", { maximumFractionDigits: 1 })})`;
  return pct + abs;
}

/** Sensitivity step label, e.g. "+10%" or "+10% (+$78/t)". */
export function stepLabel(point: any, unit: string): string {
  const s = point.step_pct > 0 ? "+" : point.step_pct < 0 ? "−" : "";
  const base = `${s}${Math.abs(point.step_pct)}%`;
  if (unit !== "USD/t") return base;
  return `${base} (${s}$${Math.abs(point.slider_value).toLocaleString("en-US", { maximumFractionDigits: 0 })}/t)`;
}

/** The sensitivity point the current slider sits on, if any. */
export function currentStep(points: any[], current: number | null | undefined): number | null {
  if (current == null) return null;
  const hit = points.find(p => Math.abs(p.slider_value - current) < 1e-6);
  return hit ? hit.step_pct : null;
}

/** Override status text for an assumption: "profile 0.5 → 0.6 (this run)". */
export function overrideText(a: any): string {
  if (!a.changed) return a.layer === "missing" ? "missing" : "profile value";
  const from = assumptionText(a.profile_value, a.unit, a.key), to = assumptionText(a.value, a.unit, a.key);
  return `${from} → ${to} (${a.layer === "override" ? "this run" : "approved adjustment"})`;
}

/** The card's figures for the current scenario, measured as each sensitivity point is: the row's impact, and the
 *  run's minimum headroom minus the baseline's minimum headroom (both inside the selected horizon), in EUR m. */
export type CurrentFigure = { impact_eur_m: number | null; headroom_change_eur_m: number | null };

/** Sensitivity table rows. When the current driver setting equals a step, that step is marked `here`; otherwise,
 *  when the card's figure is available, a `current` row is inserted in slider order. No extra engine run. */
export function sensitivityRows(points: any[], current: number | null | undefined, figure: CurrentFigure | null) {
  const here = currentStep(points, current);
  const rows: any[] = points.map(p => ({ ...p, kind: "step", here: p.step_pct === here }));
  if (here != null || current == null || !figure) return rows;
  const at = rows.findIndex(p => p.slider_value > current);
  rows.splice(at < 0 ? rows.length : at, 0, { kind: "current", here: true, slider_value: current, ...figure });
  return rows;
}

/** Minimum headroom change of the current run vs the baseline, EUR m: (scenario min − baseline min) / USD per EUR.
 *  Null when an input is missing (shown as "—", never another measure). */
export function headroomChange(calculation: any, usdPerEur: number | null | undefined): number | null {
  const hs = calculation?.scenario_min_usd_m, hb = calculation?.baseline_min_usd_m;
  return typeof hs === "number" && typeof hb === "number" && usdPerEur ? (hs - hb) / usdPerEur : null;
}

/** Stable key for caching one row's sensitivity under one page scenario. */
export const sensitivityKey = (rowId: string, request: Record<string, unknown>) => `${rowId}|${JSON.stringify(request)}`;
