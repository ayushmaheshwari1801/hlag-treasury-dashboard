// Single place the UI talks to the backend. Base URL and token are configurable so a real deployment needs no code change:
//   VITE_API_BASE (build time) or window.__API_BASE__ (runtime, e.g. injected in index.html); token from VITE_API_TOKEN or localStorage.dashboard_token.
const BASE: string = (window as any).__API_BASE__ ?? (import.meta as any).env?.VITE_API_BASE ?? "";
const SENS = "/api/v1/sensitivity";

function token(): string | null {
  try { return localStorage.getItem("dashboard_token") ?? (import.meta as any).env?.VITE_API_TOKEN ?? null; } catch { return null; }
}

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const t = token(); if (t) headers["Authorization"] = `Bearer ${t}`;
  const res = await fetch(BASE + path, { method, headers, signal, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  let data: any = null; try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const d = data?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const api = {
  riskDashboard: () => call<any>("GET", "/api/v1/risk/dashboard"),
  riskRun: (as_of: string, scenario: Record<string, unknown>, demo: boolean, forecast_id?: string) => call<any>("POST", "/api/v1/risk/run", { as_of, scenario, demo, ...(forecast_id ? { forecast_id } : {}) }),
  riskProfile: () => call<any>("GET", "/api/v1/risk/profile"),
  riskProfileUpdate: (actor: string, values: Record<string, unknown>) => call<any>("PUT", "/api/v1/risk/profile", { actor, values }),
  riskPresets: () => call<any>("GET", "/api/v1/risk/presets"),
  reviewRun: (id: string, reviewer: string, comment: string) => call<any>("POST", `/api/v1/risk/runs/${encodeURIComponent(id)}/reviews`, { reviewer, comment }),
  runReviews: (id: string) => call<any>("GET", `/api/v1/risk/runs/${encodeURIComponent(id)}/reviews`),
  riskRecord: (id: string) => call<any>("GET", `/api/v1/risk/runs/${encodeURIComponent(id)}`),
  riskRecommendations: (run_id: string, question = "") => call<any>("POST", "/api/v1/risk/recommendations", { run_id, question, mode: question ? "question" : "briefing" }),
  riskFromSignal: (id: string, asOf: string) => call<any>("POST", `/api/v1/risk/from-signal/${encodeURIComponent(id)}?as_of=${encodeURIComponent(asOf)}`),
  marketSnapshot: () => call<any>("GET", "/api/v1/market/snapshot"),
  intelligenceEvents: () => call<any>("GET", "/api/v1/intelligence/events"),
  intelligenceSignals: () => call<any>("GET", "/api/v1/intelligence/signals"),
  intelligenceAdjustments: () => call<any>("GET", "/api/v1/intelligence/adjustments"),
  intelligenceCollectionStatus: () => call<any>("GET", "/api/v1/intelligence/collection-status"),
  intelligenceCollect: () => call<any>("POST", "/api/v1/intelligence/collect", { max_articles: 10 }),
  intelligenceForecasts: () => call<any>("GET", "/api/v1/intelligence/forecasts"),
  intelligenceForecast: (as_of: string, event_ids?: string[], horizon_days = 90) => call<any>("POST", "/api/v1/intelligence/forecast", { as_of, horizon_days, ...(event_ids?.length ? { event_ids } : {}) }),
  adjustmentDecision: (id: string, decision: string, actor: string) => call<any>("POST", `/api/v1/intelligence/adjustments/${encodeURIComponent(id)}/decision`, { decision, actor }),
  dashboard: (months: number, signal?: AbortSignal) => call<any>("GET", `/api/v1/dashboard?horizon_months=${months}&news_limit=20`, undefined, signal),
  whatIf: (months: number, shocks: Record<string, number>, forecast?: {forecast_id?: string; scenario: any; as_of?: string}, overrides?: Record<string, number | boolean | string>, signal?: AbortSignal) =>
    call<any>("POST", "/api/v1/dashboard", { horizon_months: months, shocks, news_limit: 50, ...forecast, ...(overrides && Object.keys(overrides).length ? { overrides } : {}) }, signal),
  marketHistory: (series: string, asOf: string, range: string) => call<any>("GET", `/api/v1/market/history?series_id=${encodeURIComponent(series)}&as_of=${encodeURIComponent(asOf)}&range=${encodeURIComponent(range)}`),
  engineMarket: (asOf?: string) => call<any>("GET", `/api/v1/engine/market${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  engineCashflows: (runId: string) => call<any>("GET", `/api/v1/engine/runs/${encodeURIComponent(runId)}/cashflows`),
  engineAssumptions: () => call<any>("GET", "/api/v1/engine/assumptions"),
  engineHedgeOptions: () => call<any>("GET", "/api/v1/engine/hedge-test/options"),
  engineHedgeTest: (body: Record<string, unknown>) => call<any>("POST", "/api/v1/engine/hedge-test", body),
  engineMethodology: (rowId: string, runId: string, months: number) => call<any>("POST", "/api/v1/engine/methodology", { row_id: rowId, run_id: runId, horizon_months: months }),
  engineSensitivity: (body: Record<string, unknown>) => call<any>("POST", "/api/v1/engine/sensitivity", body),
  recommendationAgent: (body: Record<string, unknown>) => call<any>("POST", "/api/v1/recommendations/agent", body),
  recommendationSessions: (limit = 20) => call<any>("GET", `/api/v1/recommendations/agent?limit=${limit}`),
  recommendationSession: (id: string) => call<any>("GET", `/api/v1/recommendations/agent/${encodeURIComponent(id)}`),
  recommendationLatest: () => call<any>("GET", "/api/v1/recommendations/agent/latest"),
  activeEvents: () => call<any>("GET", "/api/v1/recommendations/active-events"),
  recommendationDecision: (sessionId: string, body: { decision_id: string; decision: string; reviewer: string; comment: string }) =>
    call<any>("POST", `/api/v1/recommendations/agent/${encodeURIComponent(sessionId)}/decisions`, body),
  recommendationDecisions: (sessionId: string) => call<any>("GET", `/api/v1/recommendations/agent/${encodeURIComponent(sessionId)}/decisions`),
  engineRefreshMarket: (actor: string) => call<any>("POST", "/api/v1/engine/market/refresh", { actor }),
  articles: (limit = 50) => call<any>("GET", `/api/v1/news/articles?limit=${limit}`),
  article: (id: string) => call<any>("GET", `/api/v1/news/articles/${encodeURIComponent(id)}`),
  brief: () => call<any>("GET", "/api/v1/news/brief"),
  integrations: () => call<any>("GET", "/api/v1/integrations"),
  assistant: (question: string, mode: "question" | "briefing", months: number, shocks: Record<string, number>, forecast?: {forecast_id?: string; scenario: any; as_of?: string}, overrides: Record<string, number | boolean | string> = {}) =>
    call<any>("POST", "/api/v1/assistant/query", { question, mode, horizon_months: months, shocks, ...forecast, overrides }),
  assistantStatus: () => call<any>("GET", "/api/v1/assistant/status"),
  scenario: (module: string, req: any) => call<any>("POST", `${SENS}/modules/${module}/scenario`, req),
  history: (module: string, range: string, extra = "") => call<any>("GET", `${SENS}/modules/${module}/history?range=${range}${extra}`),
  moduleNews: (module: string) => call<any>("GET", `${SENS}/modules/${module}/news?limit=5`),
  review: (module: string, request: any, reviewer: string, comment: string) =>
    call<any>("POST", `${SENS}/modules/${module}/reviews`, { request, reviewer, comment: comment || null }),
  audit: (module: string) => call<any>("GET", `${SENS}/audit?module=${module}&limit=8`),
  putSource: (kind: string, actor: string, data: any) => call<any>("PUT", `${SENS}/data-sources/${kind}`, { actor, data }),
  deleteSource: (kind: string, actor: string) => call<any>("DELETE", `${SENS}/data-sources/${kind}?actor=${encodeURIComponent(actor)}`),
};
