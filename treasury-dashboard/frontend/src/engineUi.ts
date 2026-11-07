/** Pure helpers for the hlag_engine panels (no imports, so tests can load this file on its own). */

/** "10.5% of headroom"; empty when unavailable. */
export function pctOfHeadroom(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? "" : `${v.toFixed(1)}% of headroom`;
}

/** Whole-number label, e.g. 1234.56 -> "1,235". */
export function wholeLabel(v: number): string {
  const r = Math.round(v);
  return (r === 0 ? 0 : r).toLocaleString("en-US");
}

/** Recommended-action cards: one per Event (the first, highest-ranked advice), others by rule ID; then the top n. */
export function topActions(actions: any[], n = 3): any[] {
  const seen = new Set<string>();
  const out: any[] = [];
  for (const a of actions ?? []) {
    const key = a?.event_ids?.length ? `event:${a.event_ids[0]}` : `rule:${a?.rule_id}`;
    if (seen.has(key)) continue;
    seen.add(key); out.push(a);
  }
  return out.slice(0, n);
}

const MONEY = /^(USD|EUR)\b/;

function niceStep(x: number): number {
  if (!(x > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(x)), m = x / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

/** Slider settings for one engine assumption, in the units the override expects (money shown in millions). */
export function sliderSpec(p: any): null | { min: number; max: number; step: number; scale: number } {
  if (typeof p?.value !== "number" || !Array.isArray(p.allowed_range)) return null;
  const [min, max] = p.allowed_range as number[];
  const scale = MONEY.test(p.unit ?? "") ? 1e6 : 1;
  const integer = !isFraction(p.key, p.unit) && !(min >= 0 && max <= 1) && [p.value, min, max].every(Number.isInteger);
  const raw = niceStep((max - min) / 100);
  return { min, max, step: integer ? Math.max(scale === 1 ? 1 : scale, raw) : raw, scale };
}

/** Readable names for the engine's adjustable assumptions; the raw key stays visible in small grey text. */
export const ASSUMPTION_NAME: Record<string, string> = {
  "liquidity.minimum_operational_reserve_usd": "Minimum operating reserve",
  "liquidity.margin_eligible_share_of_T1": "Margin-eligible share of operating cash",
  "freight.growth_g": "Volume growth",
  "freight.contract_share": "Contract share",
  "freight.index_linked_share": "Index-linked share",
  "freight.beta": "Spot-rate pass-through",
  "freight.pricing_lag_days": "Pricing lag",
  "freight.kappa": "Index-linked rate pass-through",
  "freight.clause_lag_days": "Index-linked clause lag",
  "freight.gamma": "Contract repricing share",
  "freight.leakage_gap_down": "Leakage trigger, downside",
  "freight.leakage_gap_up": "Leakage trigger, upside",
  "freight.leakage_share_down": "Volume moving to spot, downside",
  "freight.leakage_share_up": "Volume moving to spot, upside",
  "freight.leakage_volume_loss": "Volume lost on downside leakage",
  "freight.non_usd_share.ASIA_EUROPE": "Non-USD invoicing, Asia–Europe (EUR)",
  "freight.non_usd_share.ASIA_AMERICA": "Non-USD invoicing, Asia–America (CNY)",
  "freight.non_usd_share.EUROPE_AMERICA": "Non-USD invoicing, Europe–America (EUR)",
  "freight.non_usd_share.AFRICA_INTRA": "Non-USD invoicing, Africa and intra-regional (EUR)",
  "freight.delay_affected_share": "Revenue delayed by transit delays",
  "freight.collection_lag_days": "Collection lag",
  "bunker.hub_weight_sin": "Singapore hub weight",
  "bunker.scrubber_share": "Scrubber (HSFO) share",
  "bunker.supplier_lag_days": "Supplier payment lag",
  "bunker.mfr_share": "Marine fuel recovery (MFR) share",
  "bunker.mfr_lag_months": "MFR reference-price lag",
  "bunker.asia_europe_consumption_share": "Asia–Europe share of consumption",
  "bunker.beta_fallback": "Fuel-price sensitivity to Brent",
  "bunker.margin_factor": "Swap margin factor",
  "rates.floating_split_usd": "USD share of floating debt",
  "rates.floating_tranches_per_currency": "Floating tranches per currency",
  "rates.spread_usd_bp": "USD floating spread over SOFR",
  "rates.spread_eur_bp": "EUR floating spread over €STR",
  "rates.fixed_rate_pct": "Fixed rate on fixed-rate debt",
  "rates.interest_bearing_cash_share": "Interest-bearing share of cash",
  "rates.deposit_split_usd": "USD share of deposits",
  "rates.deposit_cohorts_per_currency": "Deposit cohorts per currency",
  "rates.deposit_spread_bp": "Deposit yield below benchmark",
  "rates.new_issue_premium_bp": "New-issue premium over BB OAS",
  "rates.swap_spread_bp": "Swap spread over 2-year yield",
  "rates.zim_tranche_on": "ZIM tranche included",
  "rates.zim_spread_bp": "ZIM tranche spread over SOFR",
  "fx.eur_operating_cost_per_quarter_eur": "EUR operating costs per quarter",
  "fx.inr_brl_obligations_on": "INR and BRL obligations included",
  "fx.obligation_shift_months": "INR and BRL payment shift",
  "fx.hedge_ratio.cad": "Hedge ratio, CAD",
  "fx.hedge_ratio.eur_debt": "Hedge ratio, EUR debt",
  "fx.hedge_ratio.inr_brl": "Hedge ratio, INR and BRL",
  "fx.hedge_ratio.eur_operating_costs": "Hedge ratio, EUR operating costs",
  "fx.hedge_ratio.eur_dividend": "Hedge ratio, EUR dividend",
  "adjustments.max_review_days": "Longest adjustment review window",
};

/** Readable name, falling back to the key's last part in words ("pricing_lag_days" -> "Pricing lag days"). */
export function assumptionName(key: string): string {
  if (ASSUMPTION_NAME[key]) return ASSUMPTION_NAME[key];
  const last = key.split(".").slice(1).join(" ").replace(/_/g, " ") || key;
  return last[0].toUpperCase() + last.slice(1);
}

/** Shares and fractions are shown as percentages; coefficients (beta, kappa, factors) stay as plain numbers. */
export const isFraction = (key: string | undefined, unit?: string | null) =>
  (unit ?? "").startsWith("fraction") || /share|leakage|\.gamma$/.test(key ?? "");

/** A value with its unit, e.g. "USD 750m", "70%", "2% per year", "28 days", "On". Display only; overrides keep the engine's units. */
export function assumptionText(value: unknown, unit?: string | null, key?: string): string {
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (typeof value !== "number") return String(value ?? "—");
  if (unit && MONEY.test(unit)) {
    const [ccy, ...rest] = unit.split(" ");
    return `${ccy} ${(value / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 })}m${rest.length ? " " + rest.join(" ") : ""}`;
  }
  if (isFraction(key, unit)) {
    const pct = Number((value * 100).toPrecision(6)).toLocaleString("en-US", { maximumFractionDigits: 2 });
    const rest = (unit ?? "").replace(/^fraction\s*/, "");
    return `${pct}%${rest ? " " + rest : ""}`;
  }
  const s = Number(value.toPrecision(6)).toLocaleString("en-US", { maximumFractionDigits: 4 });
  return unit ? `${s} ${unit}` : s;
}

/** Overrides that differ from the profile value, for the "changed in this run" summary. */
export function changedOverrides(params: any[], overrides: Record<string, unknown>) {
  const byKey = new Map((params ?? []).map(p => [p.key, p]));
  return Object.entries(overrides ?? {}).filter(([k, v]) => byKey.has(k) && byKey.get(k).value !== v)
    .map(([key, to]) => ({ key, from: byKey.get(key).value, to, unit: byKey.get(key).unit }));
}

/** Assumption parameters grouped by area (key prefix), in a fixed order. */
export function groupAssumptions(params: any[]) {
  const order = ["liquidity", "freight", "bunker", "rates", "fx"];
  const groups = new Map<string, any[]>();
  for (const p of params ?? []) {
    const g = p.key.split(".")[0];
    groups.set(g, [...(groups.get(g) ?? []), p]);
  }
  return [...groups.entries()].sort(([a], [b]) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
    .map(([id, items]) => ({ id, items }));
}

/** USD m to EUR m for display at the page's T0 USD/EUR rate (USD per EUR). */
export const toEur = (usd: number | null | undefined, rate: number | null | undefined) =>
  usd == null || !rate ? null : usd / rate;

/** by_driver_month rows as a driver x month table in EUR m, for one field (baseline_usd_m, scenario_usd_m, delta_usd_m). */
export function pivotCashflows(rows: any[], field: string, rate: number, horizonEnd?: string) {
  const end = horizonEnd?.slice(0, 7);
  const kept = (rows ?? []).filter(r => !end || r.month <= end);
  const months = [...new Set(kept.map(r => r.month as string))].sort();
  const order = ["freight", "bunker", "rates", "fx"];
  const drivers = [...new Set(kept.map(r => r.driver as string))].sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  const cells: Record<string, Record<string, number>> = {};
  const monthTotals: Record<string, number> = {};
  const driverTotals: Record<string, number> = {};
  for (const r of kept) {
    const v = r[field] / rate;
    (cells[r.driver] ??= {})[r.month] = (cells[r.driver]?.[r.month] ?? 0) + v;
    monthTotals[r.month] = (monthTotals[r.month] ?? 0) + v;
    driverTotals[r.driver] = (driverTotals[r.driver] ?? 0) + v;
  }
  const total = Object.values(driverTotals).reduce((a, b) => a + b, 0);
  return { months, drivers, cells, monthTotals, driverTotals, total };
}

/** Market series grouped for the card grid. */
export function groupSeries(series: any[]) {
  const group = (id: string) => id.startsWith("FX_") ? "FX" : id.startsWith("WCI_") ? "Freight" : /^(VLSFO|HSFO|BRENT)/.test(id) ? "Fuel and oil" : "Rates and credit";
  const order = ["FX", "Rates and credit", "Fuel and oil", "Freight"];
  return order.map(g => ({ id: g, items: (series ?? []).filter(s => group(s.series_id) === g) })).filter(g => g.items.length);
}

/** True for series loaded from the hand-collected weekly CSV, not fetched live (WCI and fuel). */
export const notFetchedLive = (id: string) => /^(WCI_|VLSFO_|HSFO_)/.test(id);

/** "No GBP cash flows modelled · memo only" for a row or slider whose currencies have only a translation memo position. */
export function memoOnlyText(memo: any): string {
  return memo?.memo_only ? `${(memo.currencies ?? []).join("/")} balance-sheet exposure only · no scheduled cash payments` : "";
}

/** "Memo: balance-sheet revaluation −€2.1m (not cash)" for the current scenario; empty without a memo position. */
export function memoLine(memo: any): string {
  const v = memo?.revaluation_eur_m;
  if (typeof v !== "number" || !Number.isFinite(v)) return "";
  const r = Number(v.toFixed(1));
  const sign = r < 0 ? "\u2212" : r > 0 ? "+" : "";
  return `Memo: balance-sheet revaluation ${sign}\u20ac${Math.abs(r).toFixed(1)}m (not cash)`;
}

/** Hedge settlement and margin cash by month in one table (EUR m at the T0 rate); months missing on one side are 0. */
export function hedgeMonths(settlement: { month: string; usd_m: number }[], margin: { month: string; usd_m: number }[], rate: number) {
  const rows = new Map<string, { month: string; settlement: number; margin: number }>();
  const add = (list: any[], key: "settlement" | "margin") => (list ?? []).forEach(x => {
    const row = rows.get(x.month) ?? { month: x.month, settlement: 0, margin: 0 };
    row[key] += x.usd_m / rate; rows.set(x.month, row);
  });
  add(settlement, "settlement"); add(margin, "margin");
  return [...rows.values()].sort((a, b) => a.month.localeCompare(b.month));
}

/** Clamp a hedge input to the engine's allowed range; integers for tenor. */
export function clampTo(v: number, range: number[] | undefined, integer = false): number {
  const [lo, hi] = range ?? [-Infinity, Infinity];
  const x = Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo));
  return integer ? Math.round(x) : x;
}

const eurAmount = (usd: number, rate: number) => `€${Math.round(Math.abs(usd) / rate).toLocaleString("en-US")}m`;
const fmtDate = (iso?: string | null) => {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00Z");
  return isNaN(+d) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
};

const DRIVER_NAME: Record<string, string> = { freight: "Freight", bunker: "Bunker", rates: "Rates", fx: "FX", direct: "Direct cash" };
const lowerName = (name: string) => name === "FX" ? "FX" : name.toLowerCase();

/** Desk exceptions in plain language: label, sentence (EUR at the T0 rate, USD in usdTip) and a grey explanation.
 *  cardImpact is the driver's figure on the cash-impact card for the selected horizon, already formatted. */
export function deskExceptionText(e: any, rate: number, engineHorizonEnd?: string | null,
                                  opts: { months?: number; cardImpact?: string | null } = {}) {
  const usd = e?.gross_adverse_usd_m;
  const amount = typeof usd === "number" && rate ? eurAmount(usd, rate) : "—";
  const usdTip = typeof usd === "number" ? `USD ${Math.abs(usd).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}m (EUR at the T0 rate ${rate})` : "";
  const end = engineHorizonEnd ? ` (to ${fmtDate(engineHorizonEnd)})` : "";
  if (e?.kind === "driver") {
    const name = DRIVER_NAME[e.driver] ?? String(e.driver), lower = lowerName(name);
    const months = opts.months ? `${opts.months} months` : "horizon";
    const card = opts.cardImpact ? ` (${lower}: ${opts.cardImpact})` : "";
    return { label: `Desk exception · ${name}`, usdTip,
      text: `${lower} alone could cost up to ${amount}, ${Number(e.pct_of_headroom ?? 0).toFixed(1)}% of the cash cushion${e.date ? `, by ${fmtDate(e.date)}` : ""}.`,
      measure: `This looks further ahead than the cards: it covers the full forecast period${end}, while the cards above show only the selected ${months}${card}. It counts ${lower}'s losses on their own, before other drivers offset them, at the date its total losses are largest.` };
  }
  if (e?.kind === "margin") {
    return { label: "Desk exception · Margin", usdTip,
      text: `Margin calls on the tested hedge would exceed the cash set aside for them${e.date ? ` from ${fmtDate(e.date)}` : ""}, by up to ${amount}.`,
      measure: `This covers the full forecast period${end}. The cash set aside is the share of operating cash that can be used for margin, less the margin already posted; the amount is the largest gap.` };
  }
  const series = (e?.series ?? []).join(", ") || "Some market";
  return { label: "Desk exception · Data", usdTip: "", text: `${series} data is stale or missing, so this result may be unreliable.`, measure: "" };
}

/** Tooltip for "headroom at risk" on the cash-impact card. */
export function headroomAtRiskTip(months: number, horizonEnd?: string | null) {
  return `Headroom at risk = this driver's adverse cash impact within the selected horizon (next ${months} months${horizonEnd ? `, to ${fmtDate(horizonEnd)}` : ""}) ÷ the cash cushion (baseline minimum headroom) over the same horizon.\nDesk exceptions look further ahead: the full forecast period, each driver's losses on their own before other drivers offset them, at the date they are largest.`;
}

/** A USD m amount as whole EUR m at the T0 rate with its sign, plus the USD figure for a tooltip. */
export function eurWithUsd(usd: number | null | undefined, rate: number) {
  if (typeof usd !== "number" || !Number.isFinite(usd) || !rate) return { text: "—", tip: "" };
  const r = Math.round(usd / rate);
  return { text: `${r < 0 ? "\u2212" : ""}€${Math.abs(r).toLocaleString("en-US")}m`,
           tip: `USD ${usd < 0 ? "\u2212" : ""}${Math.abs(usd).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}m (EUR at the T0 rate ${rate})` };
}
