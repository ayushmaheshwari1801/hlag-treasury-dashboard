export const MINUS = "−";
export function num(v: number | null | undefined, d = 1): string {
  if (v == null) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return (v < 0 && Number(s.replace(/,/g, "")) !== 0 ? MINUS : "") + s;
}
export function eur(v: number | null | undefined, d = 1, suffix = "m"): string {
  if (v == null) return "—";
  const sign = v < 0 ? MINUS : v > 0 ? "+" : "";
  return `${sign}€${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}${suffix}`;
}
export function pct(v: number | null | undefined, d = 1, signed = true): string {
  if (v == null) return "—";
  return `${v < 0 ? MINUS : signed && v > 0 ? "+" : ""}${Math.abs(v).toFixed(d)}%`;
}
export function when(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso); if (isNaN(+d)) return iso;
  const o: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Singapore" };
  const date = d.toLocaleDateString("en-GB", o);
  if (!withTime) return date;
  return `${date}, ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Singapore" })} SGT`;
}
export const METRIC_LABEL: Record<string, string> = {
  fx_eur: "EUR / USD", fx_gbp: "GBP / USD", fx_cny: "CNY / USD", fx_cad: "CAD / USD", interest: "Euro short-term rate",
  bunker: "Bunker fuel price", freight: "Freight rate (WCI)",
};
export const METRIC_SUB: Record<string, string> = {
  interest: "(€STR overnight)", bunker: "(VLSFO 0.5% Rotterdam)",
};
export function metricValue(id: string, v: number | null): string {
  if (v == null) return "—";
  if (id === "interest") return `${v.toFixed(2)}%`;
  if (id === "bunker") return `$${Math.round(v).toLocaleString("en-US")} / mt`;
  if (id === "freight") return `$${Math.round(v).toLocaleString("en-US")} / FEU`;
  return v.toFixed(id === "fx_cny" ? 4 : 3);
}
function roundedMarketMove(m: any): number | null {
  const value = m.id === "interest" ? m.change : m.change_pct;
  if (typeof m.change !== "number" || !Number.isFinite(m.change) || typeof value !== "number" || !Number.isFinite(value)) return null;
  const digits = m.id === "interest" ? 1 : m.id === "fx_cny" ? 2 : 1;
  const magnitude = Number(Math.abs(value).toFixed(digits));
  return magnitude === 0 ? 0 : value < 0 ? -magnitude : magnitude;
}
export function marketArrow(m: any): string {
  const change = roundedMarketMove(m);
  return change == null || change === 0 ? "" : change > 0 ? "▲" : "▼";
}
export function cashImpactTone(value: number | null | undefined): "favourable" | "adverse" | "neutral" | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value > 0 ? "favourable" : value < 0 ? "adverse" : "neutral";
}
export function metricChange(m: any): string {
  const change = roundedMarketMove(m);
  if (change == null) return "—";
  if (m.id === "interest") return `${change > 0 ? "+" : change < 0 ? MINUS : ""}${Math.abs(change)} bps`;
  return pct(change, m.id === "fx_cny" ? 2 : 1);
}
// row id -> engine module and request options
export function rowRequest(id: string, days: number, shock?: number) {
  const hs = shock === undefined ? {} : { headline_shock: shock };
  if (id.startsWith("fx_")) return { module: "fx", req: { overrides: { horizon_days: days }, options: { currency: id.slice(3).toUpperCase(), basis: "observed_move" }, ...hs } };
  if (id === "interest") return { module: "interest", req: { ...hs } };
  return { module: id, req: { overrides: { horizon_days: days }, ...hs } };
}
export const moduleOf = (id: string) => (id.startsWith("fx_") ? "fx" : id);
export const MODULE_NAME: Record<string, string> = { fx: "FX", interest: "Interest rates", bunker: "Bunker", freight: "Freight" };
function roundedShockMove(v: number | null | undefined, unit: string): number | null {
  if (v == null || !Number.isFinite(v)) return null;
  const a = Math.abs(v);
  const digits = unit === "bps" || unit === "USD/t" || a % 1 === 0 ? 0 : a < 10 ? 2 : 1;
  const magnitude = Number(a.toFixed(digits));
  return magnitude === 0 ? 0 : v < 0 ? -magnitude : magnitude;
}
export function shockText(v: number | null | undefined, unit: string): string {
  const change = roundedShockMove(v, unit);
  if (change == null) return "\u2014";
  const s = change > 0 ? "+" : change < 0 ? MINUS : "";
  const a = Math.abs(v!);
  if (unit === "bps") return `${s}${Math.round(a)} bps`;
  if (unit === "USD/t") return `${s}$${Math.round(a)}/t`;
  return `${s}${a % 1 === 0 ? a.toFixed(0) : a.toFixed(a < 10 ? 2 : 1)}%`;
}
export const ROW_LABEL: Record<string, string> = {
  fx_cad: "CAD/USD", fx_eur: "EUR/USD", fx_gbp: "GBP/USD", fx_cny: "CNY/USD", interest: "Interest rates", bunker: "Bunker fuel", freight: "Freight rate",
};
