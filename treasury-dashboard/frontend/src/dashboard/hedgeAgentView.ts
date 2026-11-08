// Pure helpers for the hedge recommendation agent panel (POST /api/v1/recommendations/agent).
// Amounts are the engine's USD millions, shown as given: the browser does not recalculate them.
const MINUS = "−";

export function usd(v: number | null | undefined, d = 1, signed = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const body = Math.abs(v).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  const sign = v < 0 ? MINUS : signed && v > 0 ? "+" : "";
  return `${sign}USD ${body}m`;
}

/** Display conversion only; saved engine amounts remain in USD. */
export function eurAmount(v: number | null | undefined, rate: number | undefined, d = 1, signed = false): string {
  if (v == null || !Number.isFinite(v) || !rate || !Number.isFinite(rate) || rate <= 0) return "—";
  return usd(v / rate, d, signed).replace("USD", "EUR");
}

export function assessmentIsStale(savedKey: string | null, currentKey: string): boolean {
  return savedKey !== currentKey;
}

export function pctText(v: number | null | undefined, d = 0): string {
  return v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(d)}%`;
}

export const GRADE: Record<string, { label: string; cls: string; help: string }> = {
  unquantified: { label: "Effect not quantified", cls: "", help: "Review proposal; no engine-tested cash benefit is claimed." },
  resolves_breach: { label: "Resolves breach", cls: "ok", help: "Without the hedge the reserve is breached; with it, it is not." },
  material: { label: "Material", cls: "ok", help: "Restores at least 25% of the headroom the scenario consumes (illustrative threshold)." },
  marginal: { label: "Marginal", cls: "warn", help: "Improves minimum headroom by less than 25% of what the scenario consumes." },
  not_needed: { label: "Not needed", cls: "", help: "The scenario does not consume headroom." },
  no_effect: { label: "No effect", cls: "", help: "No material change in the scenario minimum headroom." },
  counterproductive: { label: "Counterproductive", cls: "bad", help: "Lower headroom or a margin-eligible shortfall with the hedge." },
};
export const gradeOf = (g: string) => GRADE[g] ?? { label: g, cls: "", help: "" };

export const ORIGIN: Record<string, { label: string; help: string }> = {
  hybrid: { label: "Scenario assessment", help: "Actions are prioritized by their effect on liquidity and the available news evidence." },
  rule_based: { label: "Standard assessment", help: "Uses standard monitoring criteria and hedge comparisons. Extended news interpretation is unavailable." },
};
export const originOf = (o: string) => ORIGIN[o] ?? { label: o, help: "" };

export const MODULE: Record<string, string> = { bunker: "Bunker fuel", rates: "Interest rates", fx: "FX", freight: "Freight" };

const TARGET: Record<string, string> = {
  fuel_all: "blended fuel", vlsfo: "VLSFO", hsfo: "HSFO", floating_debt_usd: "USD floating debt",
  floating_debt_eur: "EUR floating debt", deposits_usd: "USD deposits", deposits_eur: "EUR deposits",
};

/** Short description of a tested hedge from its engine spec. */
export function hedgeLabel(c: { lever: string; spec: any }): string {
  const s = c.spec ?? {};
  if (c.lever === "hedge_ratio_change") {
    const name = String(s.key ?? "").replace("fx.hedge_ratio.", "").replace(/_/g, " ");
    const from = s.from == null ? "current" : `${Math.round(s.from * 100)}%`;
    return `FX hedge ratio · ${name}: ${from} → ${Math.round((s.to ?? 0) * 100)}%`;
  }
  const inst = s.instrument === "fixed_price_supply" ? "Fixed-price supply" : "Swap";
  return `${inst} · ${s.coverage_pct}% of ${TARGET[s.target] ?? s.target} · ${s.tenor_months} months`;
}

/** Default news window: the 7 days up to the as-of date. */
export function defaultWindow(asOf: string, days = 7): { from: string; to: string } {
  const to = new Date(`${asOf}T00:00:00Z`);
  const from = new Date(to.getTime() - (days - 1) * 86_400_000);
  return { from: from.toISOString().slice(0, 10), to: asOf };
}

/** Request body. as_of = end of the news window, so no later news or market data is used. */
export function agentRequest(window: { from: string; to: string }, includeWhatIf: boolean,
  shocks: Record<string, number>, overrides: Record<string, unknown>, forecastInput?: {scenario: any; forecast_id?: string; as_of?: string}, horizonDays?: number,
  eventIds?: string[]) {
  // forecast_mode "auto": news assumptions from the model, like the News Tracker's own estimates (fixed rules match few Events).
  const body: Record<string, unknown> = { news_from: window.from, news_to: window.to, as_of: window.to, mode: "auto", forecast_mode: "auto" };
  // Tracker Events (top six plus pins): exactly those Events, whatever their age, still nothing after the analysis date.
  if (eventIds?.length) { delete body.news_from; body.event_ids = [...eventIds].sort(); }
  if (includeWhatIf && Object.keys(shocks).length) body.shocks = shocks;
  if (includeWhatIf && Object.keys(overrides).length) body.overrides = overrides;
  if (includeWhatIf && forecastInput) Object.assign(body, forecastInput);
  if (horizonDays) body.horizon_days = Math.min(365, horizonDays);  // the page horizon, not a What-if move
  return body;
}

/** Horizon the backend applies when none is sent (RecommendationAgentRequest.horizon_days). */
export const DEFAULT_HORIZON_DAYS = 90;

/** Most recent saved news-only session for this window, horizon and news collection, so the card can show advice
 *  without a click. Advice saved before a news refresh (or without a recorded collection) is not reused. */
export function matchingSession(history: any[], window: { from: string; to: string }, horizonDays?: number, newsRunId?: string,
  eventIds?: string[]): any | null {
  const horizon = horizonDays ? Math.min(365, horizonDays) : DEFAULT_HORIZON_DAYS;
  const sameEvents = (h: any) => eventIds?.length
    ? JSON.stringify([...(h.event_ids ?? [])].sort()) === JSON.stringify([...eventIds].sort()) && !h.news_window?.from
    : h.news_window?.from === window.from;
  return history.find(h => sameEvents(h) && h.news_window?.to === window.to && h.as_of === window.to
    && !h.custom_inputs && (h.horizon_days ?? DEFAULT_HORIZON_DAYS) === horizon
    && (!newsRunId || h.news_run_id === newsRunId)) ?? null;
}

export type Advice = { tone: "ok" | "warn" | "bad"; title: string; detail: string };

/** One-line Treasury verdict from the engine diagnosis; amounts via `money` (display currency). */
export function adviceHeadline(session: any, money: (v: number | null | undefined, d?: number, signed?: boolean) => string): Advice | null {
  const d = session?.diagnosis;
  if (!d) return null;
  const recs: any[] = session.recommendations ?? [];
  const hedges = recs.filter(r => (r.kind ?? "hedge") === "hedge");
  const reviews = recs.length - hedges.length;
  const actions = hedges.length
    ? `${hedges.length} tested hedge${hedges.length > 1 ? "s" : ""} and ${reviews} review action${reviews === 1 ? "" : "s"} below.`
    : reviews ? `No hedge is needed; ${reviews} review action${reviews === 1 ? "" : "s"} below.` : "No action is recommended.";
  const low = `${money(d.scenario_min_headroom_usd_m, 0)} on ${dayLabel(d.scenario_min_date)}`;
  if (d.first_breach_date) return { tone: "bad", title: `Act now: the liquidity reserve is breached on ${dayLabel(d.first_breach_date)}`,
    detail: `Lowest headroom ${low}. ${actions}` };
  const consumed = Number(d.headroom_consumed_usd_m ?? 0);
  if (consumed > 0.5) {
    const tier = d.shortfall_tier && d.shortfall_tier !== "within_operating_cash";
    return { tone: tier ? "bad" : "warn",
      title: tier ? `Prepare funding: headroom falls to ${low}` : `Watch: headroom falls by ${money(consumed, 0)}, liquidity stays within operating cash`,
      detail: `Lowest headroom ${low}. ${actions}` };
  }
  return { tone: "ok", title: "No liquidity pressure from the latest news", detail: `Headroom is not reduced. ${actions}` };
}

/** "6 Jan 2027" from an ISO date; the input unchanged if it is not a date. */
export function dayLabel(s: string | null | undefined, year = true): string {
  if (!s) return "—";
  const t = new Date(`${s.slice(0, 10)}T00:00:00Z`);
  return Number.isFinite(t.getTime())
    ? t.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" }) : s;
}

/** "1 Nov – 7 Nov 2026" for the window line. */
export function windowLabel(window: { from: string; to: string }): string {
  return `${dayLabel(window.from, false)} – ${dayLabel(window.to)}`;
}

export function windowProblem(window: { from: string; to: string }, today: string): string | null {
  if ([window.from, window.to].some(d => !/^20\d{2}-\d{2}-\d{2}$/.test(d) || !Number.isFinite(Date.parse(d)) || new Date(d).toISOString().slice(0, 10) !== d)) return "Enter complete dates in YYYY-MM-DD format (2000–2099).";
  if (!window.from || !window.to) return "Choose both dates.";
  if (window.from > window.to) return "The start date is after the end date.";
  if (window.to > today) return "The end date cannot be in the future.";
  return null;
}

/** Rows of the engine-result table for one candidate: [label, value, tone]. */
export function effectRows(c: any, money = usd): [string, string, "pos" | "neg" | ""][] {
  const e = c.effect ?? {}, m = c.margin_test ?? {};
  const tone = (v: number | null | undefined) => (v == null ? "" : v > 0.05 ? "pos" : v < -0.05 ? "neg" : "") as "pos" | "neg" | "";
  return [
    ["Scenario minimum headroom without the hedge", money(e.min_headroom_without_usd_m), ""],
    ["Scenario minimum headroom with the hedge", money(e.min_headroom_with_usd_m), ""],
    ["Change in minimum headroom", money(e.min_headroom_change_usd_m, 1, true), tone(e.min_headroom_change_usd_m)],
    ["Headroom the scenario consumes", money(e.headroom_consumed_by_scenario_usd_m), ""],
    ["Share of consumed headroom restored", pctText(e.share_of_consumed_restored_pct), ""],
    ["Offset of the module's net adverse cash effect", pctText(e.offset_of_driver_loss_pct) + (e.over_hedge ? " · over-hedge" : ""), e.over_hedge ? "neg" : ""],
    ["Net cash change over the horizon", money(e.net_cash_change_over_horizon_usd_m, 1, true), tone(e.net_cash_change_over_horizon_usd_m)],
    ["Cash given up if the scenario reverses", money(e.cost_if_reverses_over_horizon_usd_m), e.cost_if_reverses_over_horizon_usd_m > 0.05 ? "neg" : ""],
    ["Margin posted on T0 if the scenario reverses", money(e.mirror_margin_on_t0_usd_m, 1, true), tone(e.mirror_margin_on_t0_usd_m)],
    ["Margin-eligible liquidity test", !m.applicable ? "Not applicable (no margin calls)" : m.passed ? `Passed · lowest ${money(m.lowest_margin_eligible_usd_m)}` : `Shortfall from ${m.shortfall_date}`, m.applicable && !m.passed ? "neg" : ""],
    ["Shortfall tier without → with", `${tier(e.shortfall_tier_without)} → ${tier(e.shortfall_tier_with)}`, ""],
    ["Risk status without → with", `${e.status_without ?? "—"} → ${e.status_with ?? "—"}`, ""],
  ];
}

const TIER: Record<string, string> = {
  within_operating_cash: "within operating cash", credit_lines_needed: "credit lines needed",
  liquid_investments_needed: "liquid investments needed", reserve_breached: "reserve breached",
};
const tier = (t?: string) => (t ? TIER[t] ?? t.replace(/_/g, " ") : "—");

/** Session-level reliability notes derived from the engine flags (no confidence percentages). */
export function reliabilityNotes(session: any): string[] {
  const f = session?.diagnosis?.flags ?? {}, out: string[] = [];
  if (f.synthetic_register) out.push("Company exposures are illustrative and need confirmation against internal Treasury records.");
  if (f.proxy_based) out.push("Some market inputs are proxies.");
  if (f.uses_unconfirmed_data) out.push("Some market values are unconfirmed.");
  if (f.includes_unapproved_adjustments) out.push("The scenario includes unapproved adjustments.");
  if (f.incomplete_data) out.push("Required inputs are incomplete.");
  out.push("The news scenario is an ASSUMPTION built from saved News; slider moves are user assumptions.");
  return out;
}
