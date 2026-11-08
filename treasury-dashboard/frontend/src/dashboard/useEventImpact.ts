// "Event impact on cash": each News Event on its own, through the existing "Use as scenario" path. For every Event:
//   1. its forecast inputs (POST /api/v1/intelligence/forecast with that one Event; a saved forecast for the same
//      Event, as-of date, horizon and News run is reused, so the prediction agent is asked once);
//   2. a what-if run with only those inputs (POST /api/v1/dashboard, as "Use as scenario" sends it);
//   3. that run's liquidity path (GET /api/v1/engine/runs/{id}/cashflows), when the engine provides one.
// Nothing is combined or recalculated in the browser. Startup prepares the initial forecasts;
// missing forecasts for a changed selection or horizon are requested automatically.
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { api } from "../api";
import { eventScenarioCacheKey, eventScenarioRequest, savedEventForecast } from "../eventScenario";

/** queued: not processed yet; needs_model: no saved forecast and a model call needs an explicit click. */
export type ImpactStatus = "queued" | "needs_model" | "forecasting" | "calculating" | "ready" | "no_inputs" | "error";
export interface PathPoint { date: string; value: number }
export interface EventImpact { eventId: string; status: ImpactStatus; forecast?: any; finance?: any; path?: PathPoint[] | null; flows?: any[] | null; error?: string }

interface Options {
  eventIds: string[];
  asOf?: string;
  horizonDays?: number;
  months: number;
  newsRunId: string;
  overrides: Record<string, number | boolean | string>;
  /** true: a forecast model is configured; false: fixed rules only; null: not known yet. */
  modelConfigured: boolean | null;
  forecastCache: MutableRefObject<Map<string, any>>;
  rate?: number;
  horizonEnd?: string;
}

/** Liquidity path in EUR m (engine amounts are USD m, displayed at the T0 rate), cut at the selected horizon. */
export function liquidityPath(points: any[] | undefined, rate: number, horizonEnd?: string): PathPoint[] {
  const end = horizonEnd ?? "9999";
  return (points ?? []).filter(p => p.date <= end && Number.isFinite(p.liquidity_usd_m)).map(p => ({ date: p.date, value: p.liquidity_usd_m / rate }));
}

const errorText = (_e: unknown) => "The estimate is unavailable. Retry this assessment; completed estimates will be kept.";

export function useEventImpact({ eventIds, asOf, horizonDays, months, newsRunId, overrides, modelConfigured, forecastCache, rate, horizonEnd }: Options) {
  const [items, setItems] = useState<Record<string, EventImpact>>({});
  const [attempt, setAttempt] = useState(0);
  const sequence = useRef(0);
  const saved = useRef<{ key: string; list: Promise<any[]> } | null>(null);
  const ids = [...eventIds];
  const key = asOf && horizonDays && ids.length && modelConfigured !== null && rate
    ? JSON.stringify([ids, asOf, horizonDays, months, newsRunId, overrides, horizonEnd]) : "";

  useEffect(() => {
    if (!key || !asOf || !horizonDays || !rate) return;
    const controller = new AbortController();
    const seq = ++sequence.current, current = () => seq === sequence.current && !controller.signal.aborted;
    const state: Record<string, EventImpact> = Object.fromEntries(ids.map(id => [id, { eventId: id, status: "queued" as ImpactStatus }]));
    const publish = (id: string, patch: Partial<EventImpact>) => {
      state[id] = { ...state[id], ...patch };
      if (current()) setItems({ ...state });
    };
    if (current()) setItems({ ...state });
    // Saved forecasts are listed once per News run, as-of date and horizon.
    const listKey = JSON.stringify([newsRunId, asOf, horizonDays]);
    if (saved.current?.key !== listKey) saved.current = { key: listKey, list: api.intelligenceForecasts().then(r => r.forecasts ?? []).catch(() => []) };
    void (async () => {
      const savedList = await saved.current!.list;
      for (const id of ids) {
        if (!current()) return;
        try {
          const request = eventScenarioRequest(id, asOf, horizonDays);
          const cacheKey = eventScenarioCacheKey(id, newsRunId, asOf, horizonDays);
          let forecast = forecastCache.current.get(cacheKey)
            ?? savedEventForecast(savedList, id, request.as_of, request.horizon_days, newsRunId, modelConfigured);
          if (!forecast) {
            publish(id, { status: "forecasting" });
            forecast = await api.intelligenceForecast(request.as_of, request.event_ids, request.horizon_days);
          }
          forecastCache.current.set(cacheKey, forecast);
          if (!current()) return;
          if (!forecast.predictions?.length) { publish(id, { status: "no_inputs", forecast }); continue; }
          publish(id, { status: "calculating", forecast });
          const result = await api.whatIf(months, {}, { forecast_id: forecast.forecast_id, scenario: forecast.scenario, as_of: forecast.as_of }, overrides, controller.signal);
          const finance = result.finance, runId = finance?.calculation?.run_id;
          let path: PathPoint[] | null = null, flows: any[] | null = null;
          if (runId) {
            // One read of the run's cash flows: its liquidity path and its cash lines by driver and day (for the timing table).
            const c = await api.engineCashflows(runId).catch(() => null);
            path = c?.liquidity ? liquidityPath(c.liquidity.scenario_path, rate, horizonEnd) : null;
            flows = c?.by_driver_day ?? null;
          }
          publish(id, { status: "ready", finance, path, flows });
        } catch (e) {
          publish(id, { status: "error", error: errorText(e) });
        }
      }
    })();
    return () => { controller.abort(); ++sequence.current; };
  }, [key, attempt]);

  const list = Object.values(items);
  const waiting = list.filter(item => item.status === "needs_model").length;
  return {
    items,
    /** Events without a saved forecast while a model is configured: estimating them needs an explicit click. */
    waiting,
    failed: list.filter(item => item.status === "error").length,
    running: list.some(item => item.status === "forecasting" || item.status === "calculating"),
    retry: useCallback(() => { saved.current = null; setAttempt(n => n + 1); }, []),
    forecastFor: (eventId: string) => items[eventId]?.forecast,
  };
}

export type EventImpacts = ReturnType<typeof useEventImpact>;
