# Agentic Treasury Dashboard

The active workspace integrates `../news-engine-v8` with shared market feeds, news-driven predictive what-if inputs, the deterministic Treasury cash engine and the retained React interface.

- `gateway/`: cash engine, v8 adapter, prediction agent, signal router, approved profile adjustments, immutable run records and recommendation agent.
- `frontend/`: the original four-column webpage, with v8 news and forecasts integrated into its existing cards and drawers. This is the only frontend entry point.
- `sensitivity-engine/`: existing sensitivity backend, retained for the reference page.
- `../news-engine-v8/`: the sole active news engine. The retired pre-v8 source, unused export adapter and old CI jobs have been removed; saved data remains intact.

Start from the parent workspace with `.venv/Scripts/python.exe run_dashboard.py` and open http://127.0.0.1:8080. See the [workspace quick start](../README.md) and [integration guide](docs/combined-integration.md).
# Forecast response recovery

Forecast responses tolerate JSON code fences and optional explanatory note fields.
Financial inputs, sources, dates, units and duplicate checks remain validated. A
validation failure receives at most one model correction; provider failures are not
retried. Only validated forecasts are saved. Empty predictions mean an unquantified
monitoring item, not a zero impact or a failed calculation. The dashboard can retry
unavailable estimates while reusing completed forecasts.


## Agent presentation

News analysis, forecasts, recommendations, briefings and assistant replies use bullet lists. Quantified results lead with impact and proposed review actions. Unquantified recommendations include the reason, information to confirm and next action; forecast abstentions retain their saved event-specific explanations. Historical prose is rendered as sentence bullets without dropping facts or caveats. Original source articles remain unmodified.
