import { assumptionName } from "./engineUi";

/** Keep saved analyses readable without exposing internal record references. */
export function treasuryText(value: unknown): string {
  return String(value ?? "")
    .replace(/(?<![\w.])(?:(?:spec_|risk_|forecast_|recommendation_|session_|[ARE])[a-f0-9]{16,}|(?!\d+\b)[a-f0-9]{16,})(?:_[a-f0-9]+)?\b/gi, "saved assessment")
    .replace(/(?<![\w.])-?\d+\.\d{3,}(?![\w.])/g, number => Number(number).toLocaleString("en-GB", { maximumFractionDigits: 2 }))
    .replace(/\bsynthetic(?: exposure)? register\b/gi, "illustrative company exposures")
    .replace(/\bversioned profile\b/gi, "saved company information")
    .replace(/\bengine baseline\b/gi, "baseline")
    .replace(/\bprofile-based\b/gi, "based on saved company information")
    .replace(/\btested by the engine\b/gi, "quantified in a cash-flow comparison")
    .replace(/\bengine-tested\b/gi, "calculated")
    .replace(/\bhand-collected CSV\b/gi, "Weekly market update")
    .replace(/\bcash-engine run\b/gi, "cash forecast")
    .replace(/\b(?:fx|rates|bunker|freight|liquidity|working_capital)(?:\.[a-z][a-z0-9_]*)+\b/g, assumptionName);
}

/** Keep the economic assumption, without implementation references. */
export function assumptionExplanation(value: unknown): string {
  return treasuryText(value)
    .replace(/\bMFR share sigma\b/gi, "Share of fuel costs recovered through customer surcharges")
    .replace(/\breference-price lag L\b/gi, "reference-price lag")
    .replace(/\bshare of consumption alpha\b/gi, "share of consumption")
    .replace(/\bBrent fill beta when fewer than 13 weeks of data\b/gi, "Sensitivity to Brent prices when fuel-price history is shorter than 13 weeks")
    .replace(/\((?:\d[\w.]*|[DP]\d+)(?:[,;]\s*(?:\d[\w.]*|[DP]\d+))*\)/g, "")
    .replace(/(?:[.;]\s*|^)(?:Range(?:s)? (?:equals?|proposed)|range equals)[^.]*\.?/gi, ".")
    .replace(/,\s*swept(?=\s*\.)/gi, "").replace(/\bswept\b/gi, "scenario range")
    .replace(/\bnot in spec\.?/gi, "")
    .replace(/\s+([,;.])/g, "$1").replace(/\.\s*\./g, ".").replace(/\s+/g, " ").trim();
}
