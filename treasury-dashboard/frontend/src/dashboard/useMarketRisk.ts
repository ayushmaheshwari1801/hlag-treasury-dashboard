// State of the dashboard run (GET/POST /api/v1/dashboard): What-if shocks, presets, run-only assumptions,
// News-based forecasts and News collection. Logic moved unchanged from the original App component; every cash and
// liquidity figure on the page comes from this run.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { editedForecastScenario } from "../forecastEdits";
import { eventScenarioCacheKey, eventScenarioRequest } from "../eventScenario";

export type Focus = null | { kind: "news"; id: string; modules: string[] } | { kind: "metric"; id: string; modules: string[] };

export function useMarketRisk() {
  const [months, setMonths] = useState(6);
  const [data, setData] = useState<any>(null);
  const [baseline, setBaseline] = useState<any>(null);       // observed-move view for the same horizon, for "vs observed" deltas
  const [shocks, setShocks] = useState<Record<string, number>>({});
  const [preset, setPreset] = useState("observed");
  const [busy, setBusy] = useState(false);
  const [wiOpen, setWiOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<Focus>(null);
  const [forecast, setForecast] = useState<any>(null);
  const [forecastActive, setForecastActive] = useState(false);
  const [forecastEdits, setForecastEdits] = useState<Record<string, string>>({});
  const [forecasting, setForecasting] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collectionStatus, setCollectionStatus] = useState<any>(null);
  const collectionRun = useRef<string | null>(null);
  const loadRef = useRef<() => Promise<void>>(async () => {});
  const [collectionMessage, setCollectionMessage] = useState("");
  const [collectionStage, setCollectionStage] = useState<"collecting" | "updating">("collecting");
  const [eventOptions, setEventOptions] = useState<any[]>([]);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [enginePresets, setEnginePresets] = useState<any[]>([]);   // GET /api/v1/risk/presets
  const [activePreset, setActivePreset] = useState<any>(null);     // engine preset chosen in the What-if dropdown
  const [overrides, setOverrides] = useState<Record<string, number | boolean | string>>({});  // per-run assumption overrides
  const forecastCache = useRef(new Map<string, any>());
  const seq = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    activeRequest.current?.abort();
    const controller = new AbortController(); activeRequest.current = controller;
    const my = ++seq.current; setBusy(true);
    try {
      const custom = Object.keys(shocks).length > 0 || forecastActive || !!activePreset || Object.keys(overrides).length > 0;
      if (forecastActive && Object.values(forecastEdits).some(v => !v.trim() || !Number.isFinite(Number(v)))) throw new Error("Forecast override must be a finite number.");
      const forecastInput = forecastActive && forecast ? { forecast_id: forecast.forecast_id, scenario: editedForecastScenario(forecast, forecastEdits), as_of: forecast.as_of }
        : activePreset ? { scenario: activePreset.scenario } : undefined;
      const [d, b] = await Promise.all([custom ? api.whatIf(months, shocks, forecastInput, overrides, controller.signal) : api.dashboard(months, controller.signal),
        custom && (!baseline || baseline.finance.horizon_months !== months) ? api.dashboard(months, controller.signal) : Promise.resolve(null)]);
      if (my !== seq.current) return;                       // a newer request superseded this one
      setData(d); if (!custom) setBaseline(d); else if (b) setBaseline(b); setError(null);
    } catch (e: any) { if (my === seq.current && !controller.signal.aborted) setError(e.message); }
    finally { if (my === seq.current) setBusy(false); }
  }, [months, shocks, forecast, forecastActive, forecastEdits, activePreset, overrides]);
  useEffect(() => { const t = setTimeout(load, Object.keys(shocks).length || Object.keys(overrides).length ? 180 : 0); return () => { clearTimeout(t); activeRequest.current?.abort(); }; }, [load]);
  const loadPresets = useCallback(() => api.riskPresets().then(result => setEnginePresets(result.presets ?? [])).catch(() => setEnginePresets([])), []);
  useEffect(() => { void loadPresets(); }, [loadPresets]);
  const setOverride = (key: string, v: number | boolean | string | null) =>
    setOverrides(prev => { const n = { ...prev }; if (v == null) delete n[key]; else n[key] = v; return n; });
  useEffect(() => { api.intelligenceEvents().then(result => setEventOptions(result.events ?? [])).catch(() => setEventOptions([])); }, [data?.news?.run_id, data?.news?.export]);

  // A News scenario or slider test is applied in the What-if panel beside the liquidity forecast; bring it into view.
  const scrollToStress = () => document.getElementById("position")?.scrollIntoView({ behavior: "smooth" });
  const testScenario = (t: any) => {
    setPreset("custom"); setShocks(prev => ({ ...prev, [t.slider]: t.value })); setWiOpen(true);
    scrollToStress();
  };
  const setShock = (id: string, v: number | null) => {
    setPreset("custom");
    setShocks(prev => { const n = { ...prev }; if (v == null) delete n[id]; else n[id] = v; return n; });
  };
  const forecastNews = async (eventId: string) => {
    if (forecasting || !eventId) return;
    setForecasting(true); setError(null); setWiOpen(true);
    try {
      const f = data.finance;
      const request = eventScenarioRequest(eventId, String(f.as_of).slice(0, 10), f.horizon_days ?? months * 30.4375);
      const cacheKey = eventScenarioCacheKey(eventId, data.news.run_id ?? data.news.export ?? "", request.as_of, request.horizon_days);
      let result = forecastCache.current.get(cacheKey);
      if (!result) { result = await api.intelligenceForecast(request.as_of, request.event_ids, request.horizon_days); forecastCache.current.set(cacheKey, result); }
      setForecast(result); setForecastEdits({}); setActivePreset(null); setForecastActive(true); setSelectedEventId(eventId); setShocks({}); setPreset("news_forecast");
    }
    catch (e: any) { setError(e.message); }
    finally { setForecasting(false); }
    scrollToStress();
  };
  loadRef.current = load;
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await api.intelligenceCollectionStatus();
        if (stopped) return;
        setCollectionStatus(status);
        if (status.running) collectionRun.current = status.run_id;
        else if (collectionRun.current) {
          collectionRun.current = null;
          setCollectionMessage(status.status === "interrupted" ? "News collection was interrupted. Open collection errors for recovery details. Current news is preserved." : `News collection ${status.status}.`);
          void loadRef.current();
        }
      } catch { if (!stopped) setCollectionStatus(null); }
      if (!stopped) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  const collectNews = async () => {
    if (collecting || collectionStatus?.running) return;
    setCollectionStage("collecting"); setCollecting(true); setCollectionMessage(""); setError(null);
    try {
      const result = await api.intelligenceCollect(); setCollectionStage("updating"); await load();
      setCollectionMessage(result.status === "failed" ? "News collection failed. Open collection errors for the cause; existing news is preserved." : result.errors?.length ? "News collection completed with errors. Open collection errors for details." : "News refreshed.");
    }
    catch (e: any) {
      setCollectionMessage(e.message.includes("HTTP 409")
        ? "Refresh could not start (HTTP 409). Another news collection may still be running. Current news is unchanged."
        : e.message);
    }
    finally { setCollecting(false); }
  };

  // Preset / News forecast selection handlers used by the What-if panel.
  const choosePreset = (p: any) => { setActivePreset(null); setForecastActive(false); setSelectedEventId(""); setPreset(p.id); setShocks({ ...p.shocks }); };
  const resetShocks = () => { setActivePreset(null); setForecastActive(false); setSelectedEventId(""); setPreset("observed"); setShocks({}); };
  const applyForecast = () => { setActivePreset(null); setForecastActive(true); setSelectedEventId((forecast.selected_event_ids ?? forecast.event_ids ?? [])[0] ?? ""); setShocks({}); setPreset("news_forecast"); };
  const editForecast = (i: string, v: string) => setForecastEdits(old => ({ ...old, [i]: v }));

  const custom = Object.keys(shocks).length > 0 || forecastActive || !!activePreset || Object.keys(overrides).length > 0;
  // The page's current stress scenario, as sent with the what-if run; the hedge tool and assistant use exactly this.
  const forecastInput = forecastActive && forecast ? { forecast_id: forecast.forecast_id, scenario: editedForecastScenario(forecast, forecastEdits), as_of: forecast.as_of }
    : activePreset ? { scenario: activePreset.scenario } : undefined;
  const scenarioRequest = { horizon_months: months, shocks, ...forecastInput, ...(Object.keys(overrides).length ? { overrides } : {}) };

  return {
    months, setMonths, data, baseline, shocks, setShocks, preset, setPreset, busy, wiOpen, setWiOpen, error, setError, focus, setFocus,
    forecast, forecastActive, setForecastActive, forecastEdits, forecasting, collecting, collectionStatus, collectionMessage, collectionStage,
    eventOptions, selectedEventId, setSelectedEventId, enginePresets, activePreset, setActivePreset, overrides, setOverrides, setOverride,
    load, loadPresets, testScenario, setShock, forecastNews, collectNews, choosePreset, resetShocks, applyForecast, editForecast,
    custom, forecastInput, scenarioRequest, scenarioKey: JSON.stringify(scenarioRequest),
    forecastCache,   // shared with the per-Event impact column so an Event's forecast is requested once
  };
}

export type MarketRisk = ReturnType<typeof useMarketRisk>;
