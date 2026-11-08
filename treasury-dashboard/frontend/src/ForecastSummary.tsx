import { inputChange, inputLabel } from "./dashboard/metricAssumptions";
import { treasuryText } from "./treasuryCopy";

export function unquantifiedPoints(forecast: any): string[] {
  // Reuse the event-specific explanation already saved with the assessment.
  const details = treasuryText(forecast?.outlook).split(/\n+|(?<=[.!?])\s+(?=[A-Z])/)
    .map(line => line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim())
    .filter(line => line && line.split(/\s+/).length <= 45
      && !/param_key|direct\.cash_flow|allowed input|forecasting rule|saved assessment/i.test(line));
  return [
    'Cash impact is not quantified; this does not mean there is no financial risk.',
    ...[...new Set(details)].slice(0, 4),
    ...(!details.length ? ['The current assessment does not establish a reliable amount or cash-flow timing.',
      'Confirm the company exposure, amount and expected timing before estimating an impact.',
      'Monitor new disclosures and reassess when supporting evidence becomes available.'] : []),
    'No additional cash flow is included in this scenario; keep this item under review.',
  ];
}

/** One plain line on why the inputs were chosen, from each input's saved rationale (no amounts are added).
 *  Distinct rationales are joined while the line stays short; empty when none is saved. */
export function forecastWhy(predictions: any[], maxWords = 45): string {
  const out: string[] = [];
  for (const text of [...new Set(predictions.map(p => treasuryText(p?.rationale ?? "").trim()).filter(Boolean))]) {
    const line = text.replace(/\.?$/, ".");
    if (out.length && [...out, line].join(" ").split(/\s+/).length > maxWords) break;
    out.push(line);
  }
  return out.join(" ");
}

/** Use the saved structured estimate rather than exposing model reasoning. */
export function ForecastSummary({ forecast }: { forecast: any }) {
  const predictions = forecast?.predictions ?? [];
  return <ul className="forecast-summary">
    {predictions.length ? <>
      {predictions.slice(0, 3).map((p: any, i: number) => <li key={i}>{inputLabel(p.param_key)}: <b>{inputChange(p)}</b> · from {p.effective_date}.</li>)}
      {forecastWhy(predictions.slice(0, 3)) && <li>Why: {forecastWhy(predictions.slice(0, 3))}</li>}
      <li>Action: confirm exposure and timing; review the resulting cash and funding needs before acting.</li>
    </> : unquantifiedPoints(forecast).map((point, i) => <li key={i}>{point}</li>)}
  </ul>;
}
