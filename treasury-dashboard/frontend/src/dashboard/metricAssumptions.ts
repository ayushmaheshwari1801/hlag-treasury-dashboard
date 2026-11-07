// Groups the dashboard's model assumptions (engine parameters, independent of News) under the market metric they
// drive, with the one-line explanation of how that metric reaches Hapag-Lloyd's cash. News Event forecast inputs on
// the same metric are listed separately. Display only; overrides go through the existing per-run override mechanism.

export const METRIC_EXPLAIN: Record<string, string> = {
  fx_eur: "Converts EUR-denominated operating costs, debt service and dividends into USD after hedging; a weaker EUR lowers the USD cost of EUR payments.",
  fx_gbp: "Translates GBP cash flows into USD. The model has no material GBP cash exposure, so the effect is mainly balance-sheet revaluation.",
  fx_cny: "Translates CNY-invoiced freight receipts (mainly Asia–America) into USD.",
  interest: "Reprices floating-rate debt and interest-bearing cash; higher rates raise net interest paid after the fixed-rate share.",
  bunker: "Sets the cost of fuel purchases, paid after a supplier lag and partly recovered later through the Marine Fuel Recovery surcharge.",
  freight: "Moves realised freight revenue through spot and contract repricing (pass-through and lags), then collected after the receivables lag.",
};

/** Which engine parameters belong to each metric. */
const GROUPS: Record<string, { match: (key: string) => boolean; title: string }> = {
  fx_eur: { title: "EUR exposure and hedging", match: k => k.startsWith("fx.") || /^freight\.non_usd_share\.(ASIA_EUROPE|EUROPE_AMERICA|AFRICA_INTRA)$/.test(k) },
  fx_gbp: { title: "GBP exposure", match: () => false },
  fx_cny: { title: "CNY exposure", match: k => k === "freight.non_usd_share.ASIA_AMERICA" },
  interest: { title: "Debt, deposits and spreads", match: k => k.startsWith("rates.") },
  bunker: { title: "Fuel purchasing and surcharge recovery", match: k => k.startsWith("bunker.") },
  freight: { title: "Contracts, pass-through and collection", match: k => k.startsWith("freight.") && !k.startsWith("freight.non_usd_share.") },
};

export interface ModelAssumptionGroup { title: string; params: any[] }
export function modelAssumptions(metricId: string, params: any[] = []): ModelAssumptionGroup {
  const group = GROUPS[metricId];
  return group ? { title: group.title, params: params.filter(p => group.match(p.key)) } : { title: "", params: [] };
}

export interface MetricAssumption { label: string; value: string; color?: string; newsId?: string }

/** Which market metric a News forecast input moves (forecast param keys: freight.*, bunker.*, rates.*, fx.move_pct.CCY). */
export function metricOfInput(paramKey: string): string | null {
  const [driver, , subject] = paramKey.split(".");
  if (driver === "freight") return "freight";
  if (driver === "bunker") return "bunker";
  if (driver === "rates") return "interest";
  if (driver === "fx") return subject === "EUR" ? "fx_eur" : subject === "GBP" ? "fx_gbp" : subject === "CNY" ? "fx_cny" : subject === "CAD" ? "fx_cad" : null;
  return null;
}

const TRADES: Record<string, string> = { ASIA_EUROPE: "Asia–Europe", ASIA_AMERICA: "Asia–America", EUROPE_AMERICA: "Europe–America", AFRICA_INTRA: "Africa & Intraregional" };
const FIELDS: Record<string, string> = { rate_shock_pct: "Freight rate", volume_shock_pct: "Transport volume", transit_delay_days: "Collection delay",
  vlsfo_shock_pct: "VLSFO price", hsfo_shock_pct: "HSFO price", consumption_uplift_pct: "Fuel consumption", rate_path: "Interest-rate step",
  credit_spread_change_bp: "Refinancing spread", move_pct: "Currency move", cash_flow: "Direct cash" };

/** "Freight rate · Asia–Europe" for a forecast param key. */
export function inputLabel(paramKey: string): string {
  const [driver, field, subject] = paramKey.split(".");
  return `${FIELDS[field] ?? driver}${subject ? ` · ${TRADES[subject] ?? subject}` : ""}`;
}

/** "+15%", "−25 bp", "+12 days" for one forecast prediction. */
export function inputChange(p: { change: number; unit: string }): string {
  const sign = p.change > 0 ? "+" : p.change < 0 ? "−" : "";
  const unit = p.unit === "pct" ? "%" : p.unit === "bp" ? " bp" : p.unit === "usd_m" ? " USD m" : ` ${p.unit}`;
  return `${sign}${Math.abs(p.change)}${unit}`;
}

/** metric id → the News Event forecast inputs that move it (shown separately from model assumptions). */
export function newsDriversByMetric(rows: { event_id: string; title: string }[], forecastFor: (eventId: string) => any, colors: Record<string, string>) {
  const out: Record<string, MetricAssumption[]> = {};
  for (const row of rows) for (const p of forecastFor(row.event_id)?.predictions ?? []) {
    const id = metricOfInput(p.param_key);
    if (id) (out[id] ??= []).push({ newsId: row.event_id, color: colors[row.event_id], label: row.title, value: `${inputLabel(p.param_key)} ${inputChange(p)}` });
  }
  return out;
}
