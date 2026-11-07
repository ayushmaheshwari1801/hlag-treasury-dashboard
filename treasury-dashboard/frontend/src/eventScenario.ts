/** A user's Event selection produces one independent forecast, never a combined news shock. */
export function eventScenarioRequest(eventId: string, asOf: string, horizonDays: number) {
  if (!eventId.trim()) throw new Error("Select an Event to forecast.");
  return { as_of: asOf, event_ids: [eventId], horizon_days: Math.min(365, Math.max(1, Math.round(horizonDays))) };
}

export function eventScenarioCacheKey(eventId: string, newsRunId: string, asOf: string, horizonDays: number) {
  return JSON.stringify([eventId, newsRunId, asOf, Math.min(365, Math.max(1, Math.round(horizonDays)))]);
}

/** A saved forecast for exactly this Event, as-of date, horizon and News run. When a forecast model is configured,
 *  a saved fixed-rule forecast is not reused: it was made without the model, so the Event offers a model forecast. */
export function savedEventForecast(list: any[], eventId: string, asOf: string, horizonDays: number, newsRunId: string, modelConfigured: boolean | null) {
  const horizon = Math.min(365, Math.max(1, Math.round(horizonDays)));
  return list.find(x => (x.selected_event_ids ?? x.event_ids ?? []).join() === eventId && x.as_of === asOf && x.horizon_days === horizon
    && (!newsRunId || !x.source_run_id || x.source_run_id === newsRunId) && (!modelConfigured || x.mode === "model"));
}

export const EVENT_CATEGORIES = ["regulatory", "macroeconomics", "industry"];

export const NEWS_CATEGORY_LABEL: Record<string, string> = {regulatory: "Regulatory", macroeconomics: "Macro", industry: "Industry", other: "Other"};
export function groupNewsCategories<T extends {category?: string; score?: number | null; published_at?: string; title: string}>(rows: T[]) {
  return [...EVENT_CATEGORIES, "other"].map(category => ({category, label: NEWS_CATEGORY_LABEL[category],
    rows: rows.filter(row => (EVENT_CATEGORIES.includes(row.category ?? "") ? row.category : "other") === category)
      .sort((a,b) => (b.score ?? -1) - (a.score ?? -1) || (b.published_at ?? "").localeCompare(a.published_at ?? "") || a.title.localeCompare(b.title))
  })).filter(group => group.category !== "other" || group.rows.length);
}

export function topEventScenarios(events: any[]) {
  const unique = [...new Map(events.map(event => [event.event_id ?? event.id, event])).values()];
  const score = (event: any) => typeof event.score === "number" ? event.score : event.score?.total ?? -1;
  return EVENT_CATEGORIES.flatMap(category => unique.filter(event => event.category === category && score(event) >= 0)
    .sort((a, b) => score(b) - score(a) || String(b.published_at ?? "").localeCompare(String(a.published_at ?? ""))
      || String(a.event_id ?? a.id).localeCompare(String(b.event_id ?? b.id))).slice(0, 2));
}
