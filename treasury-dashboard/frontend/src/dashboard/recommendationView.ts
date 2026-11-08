// Pure helpers for the Treasury recommendations card (recommendation-agent/3). No internal ids are shown: hover text
// uses fact labels and sources only (AGENTS.md).

export const STATEMENT_LABELS = ["Fact", "Engine", "News forecast", "AI assumption", "To verify"] as const;

const LABEL_CLASS: Record<string, string> = {
  "Fact": "lbl-fact", "Engine": "lbl-engine", "News forecast": "lbl-news", "AI assumption": "lbl-ai", "To verify": "lbl-verify",
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
    test_hedge: "hedge tests", test_hedge_ratio: "hedge-ratio tests", get_liquidity_path: "liquidity path checks",
    get_options: "hedge option lookups", get_fact_detail: "fact detail lookups", submit_recommendation_set: "submissions to the checks",
  };
  const counts = new Map<string, { ok: number; refused: number }>();
  for (const c of calls ?? []) {
    const k = names[c.tool] ?? "other steps";
    const v = counts.get(k) ?? { ok: 0, refused: 0 };
    c.ok ? v.ok++ : v.refused++;
    counts.set(k, v);
  }
  return [...counts].map(([k, v]) => `${v.ok + v.refused} ${k}${v.refused ? ` (${v.refused} refused or sent back)` : ""}`);
}

export const DECISIONS = [["approve", "Approve"], ["defer", "Defer"], ["reject", "Reject"]] as const;
