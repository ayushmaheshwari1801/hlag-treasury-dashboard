export function editedForecastPredictions(forecast: any, edits: Record<string, string>) {
  return (forecast?.predictions ?? []).map((p: any, i: number) => {
    const text = edits[String(i)];
    return text !== undefined && text.trim() && Number.isFinite(Number(text)) ? { ...p, change: Number(text) } : p;
  });
}

export function editedForecastScenario(forecast: any, edits: Record<string, string>) {
  const scenario = JSON.parse(JSON.stringify(forecast?.scenario ?? {}));
  for (const [index, text] of Object.entries(edits)) {
    const prediction = forecast?.predictions?.[Number(index)];
    if (!prediction || !text.trim() || !Number.isFinite(Number(text))) continue;
    const [group, field, subject] = prediction.param_key.split(".");
    const value = prediction.unit === "pct" ? Number(text) / 100 : Number(text);
    if (prediction.param_key === "direct.cash_flow") {
      // A direct cash flow is one dated entry of scenario.direct.cash_flows (signed USD million).
      const flow = (scenario.direct?.cash_flows ?? []).find((f: any) => f.effect_id === prediction.effect_id && f.date === prediction.effective_date);
      if (flow && value !== 0) flow.amount_usd_m = value;
    } else if (group === "rates" && field === "rate_path") {
      const steps = scenario[group]?.[field]?.[subject] ?? [];
      const step = steps.find((s: any) => s.date === prediction.effective_date);
      if (step) step.step_bp = value;
    } else if (subject) scenario[group][field][subject] = value;
    else scenario[group][field] = value;
  }
  return scenario;
}

