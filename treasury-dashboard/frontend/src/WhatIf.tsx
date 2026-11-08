import { treasuryText } from "./treasuryCopy";
import { cashImpactTone, num, shockText } from "./format";
import { presetOptionValue } from "./enginePresets";
import { memoLine, memoOnlyText } from "./engineUi";

export default function WhatIf({ scenario, cashImpacts, shocks, preset, busy, onChange, onPreset, onReset, open, setOpen, deltas, forecastActive, forecastContent, forecasting, eventScenarios, selectedEventId, onSelectEvent, enginePresetGroups, activePresetId }:
  { scenario: any; cashImpacts: Record<string, number | null>; shocks: Record<string, number>; preset: string; busy: boolean; open: boolean; setOpen: (b: boolean) => void;
    onChange: (id: string, v: number | null) => void; onPreset: (p: any) => void; onReset: () => void;
    deltas: { label: string; base: number | null; now: number | null }[]; forecastActive?: boolean;
    forecastContent?: React.ReactNode; forecasting?: boolean; eventScenarios?: {event_id: string; title: string; category: string}[];
    selectedEventId?: string; onSelectEvent?: (eventId: string) => void;
    enginePresetGroups?: { id: string; label: string; presets: any[] }[]; activePresetId?: string }) {
  const custom = Object.keys(shocks).length > 0 || forecastActive || scenario.custom;
  return (
    <section className={`card whatif ${custom ? "custom" : ""}`} aria-labelledby="wi">
      <div className="wi-head">
        <button className="wi-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          <h2 id="wi">What-if scenario</h2><span className="chev2">{open ? "▴" : "▾"}</span>
        </button>
        {eventScenarios !== undefined ? <label className="event-scenario-select"><span>Scenario</span><select aria-label="Scenario" disabled={forecasting} value={forecastActive && selectedEventId ? selectedEventId : activePresetId ? presetOptionValue(activePresetId) : Object.keys(shocks).length || preset === "custom" ? "__manual__" : "__observed__"} onChange={e => onSelectEvent?.(e.target.value)}><option value="__observed__" title="Today's prices are already in the baseline; this scenario applies last week's market moves once more.">Last week's moves repeated</option><option value="__manual__">Manual scenario</option>{(enginePresetGroups ?? []).map(group => <optgroup key={group.id} label={`Standard scenarios · ${group.label}`}>{group.presets.map(p => <option key={p.id} value={presetOptionValue(p.id)} title={p.description}>{p.name}</option>)}</optgroup>)}</select></label> : <div className="presets" role="group" aria-label="Preset scenarios">
          {scenario.presets.map((p: any) => (
            <button key={p.id} className={`preset ${preset === p.id ? "on" : ""}`} title={p.description} onClick={() => onPreset(p)}>{p.label}</button>))}
        </div>}
        <div className="wi-status">
          {forecasting && <span className="muted small">Preparing Event scenario…</span>}
          {busy ? <span className="muted small">Recalculating{"…"}</span> : custom ? <span className="chip warn">SCENARIO</span> : <span className="chip" title="Today's prices are already in the baseline; this scenario applies last week's market moves once more.">Last week's moves repeated</span>}
          {custom && <button className="link small" onClick={onReset}>Reset</button>}
        </div>
      </div>
      {custom && <div className="deltas">{deltas.map(d => {
        const diff = d.now != null && d.base != null ? d.now - d.base : null;
        return <span key={d.label}>{d.label} <b>{num(d.now, 0)}</b>
          {diff != null && diff !== 0 && <em className={diff < 0 ? "neg" : "pos"}> ({diff > 0 ? "+" : "−"}{Math.abs(diff).toLocaleString("en-US", { maximumFractionDigits: 0 })} vs observed)</em>}</span>;
      })}</div>}
      {open && <div className="sliders">
        {scenario.sliders.map((s: any) => {
          const set = s.id in shocks; const v = set ? shocks[s.id] : (s.value ?? s.observed ?? 0);
          const obsPos = s.observed == null ? null : (s.observed - s.min) / (s.max - s.min) * 100;
          return (
            <div key={s.id} className={`slider ${set ? "set" : ""}`}>
              <div className="sl-top"><label htmlFor={`sl-${s.id}`}>{s.label}</label>
                <span className={`sl-val ${cashImpactTone(cashImpacts[s.id]) ?? ""}`}>{shockText(v, s.unit)}</span></div>
              <div className="sl-track">
                <input id={`sl-${s.id}`} type="range" min={s.min} max={s.max} step={s.step} value={v}
                  onChange={e => onChange(s.id, Number(e.target.value))} aria-valuetext={shockText(v, s.unit)} />
                {obsPos != null && <i className="obs" style={{ left: `${obsPos}%` }} title={`Observed 1-week move: ${shockText(s.observed, s.unit)}`} />}
              </div>
              <div className="sl-foot"><span>{shockText(s.min, s.unit)}</span>
                {set ? <button className="link" onClick={() => onChange(s.id, null)}>{forecastActive ? "forecast value" : `observed ${shockText(s.observed, s.unit)}`}</button> : <span className="muted">{forecastActive || scenario.custom ? "scenario input" : "observed"}</span>}
                <span>{shockText(s.max, s.unit)}</span></div>
              {memoOnlyText(s.memo) && <div className="memo-only small">{memoOnlyText(s.memo)}</div>}
              {memoLine(s.memo) && <div className="memo-line">{memoLine(s.memo)}</div>}
            </div>);
        })}
      </div>}
      {open && forecastActive && <p className="foot">News forecast applied. Sliders you move explicitly override that driver; other route, fuel, currency and timing estimates remain in the forecast.</p>}
      {open && forecastContent}
      {open && <p className="foot">{treasuryText(scenario.note)} FX sliders are the currency's move against USD (negative = stronger USD). The tick marks this week's observed move.</p>}
    </section>
  );
}
