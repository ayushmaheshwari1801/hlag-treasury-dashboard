import { shockText } from "./format";
import { editedForecastPredictions } from "./forecastEdits";

function inputName(key: string, p?: any) {
  if (key === "direct.cash_flow") return `Direct cash · ${p?.cash_label ?? p?.effect_id ?? "payment"}`;
  const [driver, field, subject] = key.split(".");
  const names: Record<string, string> = { rate_shock_pct: "Freight rate", volume_shock_pct: "Transport volume", transit_delay_days: "Collection delay", vlsfo_shock_pct: "VLSFO price", hsfo_shock_pct: "HSFO price", consumption_uplift_pct: "Fuel consumption", rate_path: "Interest-rate step", credit_spread_change_bp: "Refinancing spread", move_pct: "Currency move" };
  const trades: Record<string, string> = { ASIA_EUROPE: "Asia–Europe", ASIA_AMERICA: "Asia–America", EUROPE_AMERICA: "Europe–America", AFRICA_INTRA: "Africa & Intraregional" };
  return `${names[field] ?? driver}${subject ? ` · ${trades[subject] ?? subject}` : ""}`;
}

export default function NewsForecast({ forecast, edits, active, busy, onEdit, onApply }:
  { forecast: any; edits: Record<string, string>; active: boolean; busy: boolean;
    onEdit: (index: string, value: string) => void; onApply: () => void }) {
  if (!forecast || !active) return null;
  const edited = Object.entries(edits).some(([i, value]) => Number(value) !== forecast.predictions?.[Number(i)]?.change);
  return <details className="news-forecast" open>
    <summary><span className="chip">News forecast</span> {forecast.scenario?.name ?? "Event scenario"} · {forecast.predictions?.length ?? 0} inferred input changes {active && <span className="chip ok">Applied</span>}{edited && <span className="chip">Edited</span>}</summary>
    <ForecastSummary forecast={{ ...forecast, predictions: editedForecastPredictions(forecast, edits) }} />
    {!forecast.predictions?.length && <p className="small muted">This Event has no supported input changes. It remains a separate scenario with zero additional shocks.</p>}<div className="forecast-inputs">{(forecast.predictions ?? []).map((p: any, i: number) => <div key={`${p.param_key}-${p.effective_date}`} className="forecast-input">
      <div className="forecast-input-head"><label htmlFor={`forecast-${i}`}>{inputName(p.param_key, p)}</label><span className="small muted">{Math.round(p.confidence * 100)}% confidence</span></div>
      <div className="forecast-value"><input id={`forecast-${i}`} type="number" step="any" value={edits[String(i)] ?? String(p.change)} onChange={e => onEdit(String(i), e.target.value)} /><span>{p.unit === "pct" ? "%" : p.unit === "usd_m" ? "USD m" : p.unit}</span><small className="muted">Initial forecast {p.unit === "days" ? `${p.change} days` : p.unit === "usd_m" ? `${p.change > 0 ? "+" : ""}${p.change} USD m` : shockText(p.change, p.unit === "pct" ? "%" : "bps")}</small></div>
      <div className="small muted">{p.unit === "usd_m" ? `Estimated payment on ${p.effective_date}` : `${p.effective_date} · ${p.duration_days} days`}</div>
    </div>)}</div>
    <div className="forecast-footer"><span className="small muted">Initial estimates and manual overrides are saved with the calculation.</span>{!active && <button className="testbtn" disabled={busy} onClick={onApply}>Apply forecast</button>}</div>
  </details>;
}
import { ForecastSummary } from "./ForecastSummary";
