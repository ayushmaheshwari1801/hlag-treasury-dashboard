// Pure helpers for the Treasury recommendations card (recommendation-agent/3). No internal ids are shown: hover text
// uses fact labels and sources only (AGENTS.md).

export const STATEMENT_LABELS = ["Fact", "Engine", "News forecast", "Model assumption", "AI assumption", "To verify"] as const;

const LABEL_CLASS: Record<string, string> = {
  "Fact": "lbl-fact", "Engine": "lbl-engine", "News forecast": "lbl-news", "Model assumption": "lbl-model",
  "AI assumption": "lbl-ai", "To verify": "lbl-verify",
};
export const labelClass = (label?: string | null) => LABEL_CLASS[label ?? ""] ?? "lbl-fact";

const POSITION: Record<string, { text: string; cls: string }> = {
  business_as_usual: { text: "Business as usual", cls: "ok" },
  heightened_monitoring: { text: "Heightened monitoring", cls: "warn" },
  defensive: { text: "Defensive", cls: "bad" },
  opportunistic: { text: "Opportunistic", cls: "ok" },
};
export const positionOf = (value?: string) => POSITION[value ?? ""] ?? { text: value ?? "—", cls: "" };

const TRIGGER_HELP: Record<string, string> = {
  Occurred: "Has already happened in markets",
  Forecast: "Expected from the news forecast",
  Structural: "Built into the company's cash calendar or policy",
};
export function triggerText(trigger: any, when: (iso: string, withTime?: boolean) => string): string {
  if (!trigger) return "";
  const parts = [trigger.type, trigger.date ? when(trigger.date, false) : null, trigger.source].filter(Boolean);
  return parts.join(" · ");
}
export const triggerHelp = (type?: string) => TRIGGER_HELP[type ?? ""] ?? "";

/** Hover text for a statement: the label and source of every fact or result it cites (never the internal id). */
export function sourceTip(statement: any, session: any): string {
  const facts = session?.facts ?? {};
  const assumptions = Object.fromEntries((session?.assumptions ?? []).map((a: any) => [a.id, a]));
  const checks = Object.fromEntries((session?.to_verify ?? []).map((v: any) => [v.id, v]));
  const lines = (statement?.cites ?? []).map((c: string) => {
    const f = facts[c];
    if (f) return `${f.label} — ${f.source}${f.date ? `, ${f.date}` : ""} (${f.provenance})`;
    if (assumptions[c]) return `Assumption (${assumptions[c].likelihood}): ${assumptions[c].statement}`;
    if (checks[c]) return `To verify: ${checks[c].check}`;
    if (String(c).startsWith("cand_")) return "Hedge tested on the liquidity forecast";
    if (String(c).startsWith("path_")) return "Liquidity path from the forecast";
    if (c === "diag") return "Liquidity forecast diagnosis";
    return "";
  }).filter(Boolean);
  return lines.length ? Array.from(new Set(lines)).join("\n") : "No source cited";
}

/** The ticked Events: the top Events by default. */
export const defaultTicks = (active: any) => (active?.included ?? []).map((e: any) => e.event_id as string);
export const moreText = (n: number) => `${n} more Event${n === 1 ? "" : "s"} not included`;
export const forecastText = (origin?: string) => origin === "model_saved" ? "AI forecast (saved)" : "Rule-based inputs";

export const requestBody = (ticked: string[], mode: "auto" | "rule_based" = "auto") => ({ event_ids: ticked, mode });

export const isLegacy = (session: any) => !!session && !Array.isArray(session.decisions);

export function costText(session: any): string {
  const c = session?.how_reached?.ai_cost;
  if (session?.origin !== "model" || !c || !c.calls) return "No AI call (rule-based).";
  const tokens = `${(c.prompt_tokens ?? 0).toLocaleString("en-US")} input and ${(c.completion_tokens ?? 0).toLocaleString("en-US")} output tokens`;
  return c.cost_usd == null ? `${c.calls} AI calls, ${tokens}; cost not reported by the provider.`
    : `USD ${c.cost_usd.toFixed(3)} for ${c.calls} AI calls (${tokens}).`;
}

export function originText(session: any): string {
  if (!session) return "";
  return session.origin === "model" ? "AI-assisted, checked item by item" : "Rule-based (no AI)";
}

/** "Prepared at …" for a prepared session, "Assessed at …" for a live one. */
export function stampText(session: any, when: (iso: string, withTime?: boolean) => string): string {
  if (!session) return "";
  return session.prepared ? `Prepared at ${when(session.prepared_at)}` : `Assessed at ${when(session.created_at)}`;
}

export function factPackText(fp: any, when: (iso: string, withTime?: boolean) => string): string {
  if (!fp) return "";
  if (fp.status && fp.status !== "ready" && !fp.built_at) return fp.status === "building" ? "Facts are being prepared…" : "Facts not prepared yet";
  return `Facts as of ${when(fp.as_of, false)} · prepared ${when(fp.built_at)} in ${fp.build_seconds}s`;
}

/** Tool calls in plain words (no tool names or ids). */
export function toolSummary(calls: any[]): string[] {
  const names: Record<string, string> = {
    test_hedge: "hedge test", test_hedge_ratio: "hedge-ratio test", get_liquidity_path: "liquidity path check",
    get_options: "hedge option lookup", get_fact_detail: "fact detail lookup", submit_recommendation_set: "submission to the checks|submissions to the checks",
  };
  const counts = new Map<string, { ok: number; refused: number }>();
  for (const c of calls ?? []) {
    const k = names[c.tool] ?? "other step";
    const v = counts.get(k) ?? { ok: 0, refused: 0 };
    c.ok ? v.ok++ : v.refused++;
    counts.set(k, v);
  }
  return [...counts].map(([k, v]) => {
    const [one, many] = k.split("|");
    return `${plural(v.ok + v.refused, one, many)}${v.refused ? ` (${v.refused} refused or sent back)` : ""}`;
  });
}

export const DECISIONS = [["approve", "Approve"], ["defer", "Defer"], ["reject", "Reject"]] as const;

/** One line: "Prepared <time> · facts as of <date>" (or "Assessed …" for a live run). */
export function preparedLine(session: any, when: (iso: string, withTime?: boolean) => string): string {
  if (!session) return "";
  const stamp = session.prepared ? `Prepared ${when(session.prepared_at)}` : `Assessed ${when(session.created_at)}`;
  const asOf = session.fact_pack?.as_of ?? session.as_of;
  return asOf ? `${stamp} · facts as of ${when(asOf, false)}` : stamp;
}

export const CONTEXT_LINE = "Based on today's prices plus the ticked News forecasts; the header above shows the selected What-if scenario.";

/** How a ticked Event was used, in words. */
export function eventStatusText(e: any): string {
  switch (e?.status) {
    case "used_in_decision": return `Used in decision ${e.decision}`;
    case "considered_not_acted": return "Considered, not acted on";
    case "no_cash_effect": return "No cash effect in the horizon";
    case "not_quantifiable": return "Not quantifiable";
    default: return "—";
  }
}

/** "1 item", "2 items". */
export const plural = (n: number, word: string, many?: string) => `${n} ${n === 1 ? word : (many ?? word + "s")}`;

/** A fact's provenance as a page label: Fact / Model assumption / Engine / News forecast. */
export function factLabelOf(provenance?: string): string {
  if (provenance === "ENGINE") return "Engine";
  if (provenance === "NEWS") return "News forecast";
  if (provenance === "ASSUMPTION" || provenance === "PROXY") return "Model assumption";
  return "Fact";
}

const fmt1 = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const signed = (v: number, unit: string) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit}`;

/** A fact's value in plain words: "EUR 649.4m", "70%", "+12.2%", "Yes", "8 Nov 2026". */
export function factValueText(f: any, when: (iso: string, withTime?: boolean) => string): string {
  if (typeof f?.value_text === "string") return f.value_text;
  const v = f?.value;
  if (v === null || v === undefined) return "not available";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (f.unit === "USD m" && typeof f.eur_value === "number") return `EUR ${f.eur_value < 0 ? "-" : ""}${fmt1(Math.abs(f.eur_value))}m`;
  if (typeof v === "number") {
    if (f.unit === "%") return /change|move|rose|fell|over \d/i.test(String(f.label ?? "")) ? signed(v, "%")
      : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
    if (f.unit === "bp") return signed(v, " bp");
    if (f.unit === "pct") return signed(v, "%");
    if (f.unit === "days") return plural(v, "day");
    return `${v.toLocaleString("en-US", { maximumFractionDigits: 4 })}${f.unit && f.unit !== "score" ? " " + f.unit : ""}`;
  }
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return when(v, false);
  return String(v);
}

/** "Unused credit lines: EUR 649.4m · 8 Nov 2026 · Annual Report 2025". */
export function factLine(f: any, when: (iso: string, withTime?: boolean) => string): string {
  const parts = [`${f.label}: ${factValueText(f, when)}`];
  if (f.date && !(typeof f.value === "string" && f.value === f.date)) parts.push(when(f.date, false));
  if (f.source) parts.push(f.source);
  return parts.join(" · ");
}

export const FACT_GROUPS = ["Market & alerts", "News forecasts", "Liquidity forecast", "Company & policy"] as const;

/** The group a fact belongs to, from its id (ids are never shown). */
export function factGroup(id: string): (typeof FACT_GROUPS)[number] {
  if (/^F\.(mkt|alert|occ|fx)\./.test(id) || id.startsWith("F.eng.market_moves_7d")) return "Market & alerts";
  if (id.startsWith("F.news.")) return "News forecasts";
  if (/^F\.(policy|bank|trade|owner|playbook)\./.test(id) || (id.startsWith("F.zim.") && id !== "F.zim.in_forecast")) return "Company & policy";
  return "Liquidity forecast";
}

export function groupFacts(facts: any[]): { group: string; facts: any[] }[] {
  return FACT_GROUPS.map(group => ({ group, facts: (facts ?? []).filter(f => factGroup(f.id) === group) })).filter(g => g.facts.length);
}
