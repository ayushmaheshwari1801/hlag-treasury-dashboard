# Sensitivity API

Base path `/api/v1/sensitivity`. JSON in and out. Errors: `404` unknown module, `422` invalid assumption or shape
(the message names the field and bound), `401` when a bearer token is configured and missing.

| Method and path | Purpose |
| --- | --- |
| `GET /overview?as_of=` | Four cards: latest value, change, source, freshness, selected-scenario impact, status, worst-case status, reliability |
| `GET /modules/{module}/defaults` | Editable controls with bounds, defaults and provenance (what the modal renders) |
| `POST /modules/{module}/scenario?as_of=` | Recalculate. Body is a `ScenarioRequest`. Returns `result` and `explanation` |
| `POST /modules/{module}/reset?actor=` | Versioned defaults again; writes a `reset` audit entry |
| `GET /modules/{module}/history?range=1M\|3M\|6M\|1Y\|All&benchmark=` | Actual points, gaps, `interpolated: false` |
| `GET /modules/{module}/news?limit=` | Matching saved articles. Context only |
| `POST /compound` | Compound stress. Body `{compound, risk}` |
| `POST /modules/{module}/reviews` | Recompute on the server, store a snapshot and a review entry. Body `{reviewer, comment, request}` |
| `GET /audit?module=&limit=` / `GET /audit/verify` / `GET /snapshots/{id}` | Read the audit trail and check the hash chain |

`module` is one of `fx`, `interest`, `bunker`, `freight`.

## ScenarioRequest

```json
{
  "overrides": {"consumption_mt": 3.0, "recovery_fraction": 0.5},
  "shocks": null,
  "headline_shock": 50,
  "mode": null,
  "benchmark": null,
  "options": {},
  "limit": {"name": "Earnings-at-risk", "amount": 6, "currency": "EUR"},
  "confirmed_triggers": []
}
```

- `overrides` keys are listed by `/defaults`. Unknown keys, out-of-range values and non-editable keys return `422`. Overridden inputs are tagged `ILLUSTRATIVE` as user-supplied assumptions.
- `headline_shock` is the selected scenario in the module's unit: FX `%`, interest `bps`, bunker `USD/t`, freight `%`. Sending it together with a conflicting override of the same quantity is rejected.
- `shocks` replaces the default table rows. Without it, rows are the latest move times fixed multipliers. Rows outside the allowed range are omitted and listed in `warnings`.
- `mode`: interest `"A"` (earnings proxy, default) or `"B"` (cash interest); FX `"payable"` or `"receivable"`.
- `options.position_type` (interest Mode B): `floating_debt` or `floating_deposit`.
- `limit`: an approved limit. A limit adds a utilisation test to the status.
- `confirmed_triggers`: `hard_limit_breach` or `payment_shortfall`, each with `confirmed_by` and `evidence`. Without both, the trigger is ignored. A valid one gives `CRITICAL` (rule `MAT-C1`).

## ModuleResult (the fields a UI needs)

`headline` and `scenarios[]` (label, shock, impact `Quantity`, status, rule), `assessment` (status, `rule_id`, `rule_trace`, `missing`,
`liquidity_ratio`, `limit_utilisation`, `thresholds_note`), `worst_case_status`, `market` (latest, previous, change, source, observation
and retrieval timestamps, freshness, gaps, error), `inputs[]` (value, provenance, bounds), `intermediate[]`, `formula`, `methodology[]`,
`disclosed_reference[]` (never added to the scenario), `treasury_actions[]` (each with a rule ID and conditional wording),
`warnings[]`, `reliability` and `reliability_reasons`, `formula_version`, `threshold_version`, `defaults_version`.

A `Quantity` has `value`, `raw` (exact decimal string), `unit`, `currency`, `provenance` and `note`. `value: null` means `UNAVAILABLE`.

## Status rules

| Rule | Condition | Status |
| --- | --- | --- |
| `MAT-C1` | Valid confirmed trigger | CRITICAL |
| `MAT-C2` | Adverse USD impact / comparable liquidity >= 3% | CRITICAL |
| `MAT-C3` | Limit utilisation >= 100% (computed; confirm before calling it a breach) | CRITICAL |
| `MAT-H1` | Liquidity ratio >= 1% and < 3% | HIGH |
| `MAT-H2` | Limit utilisation >= 80% and < 100% | HIGH |
| `MAT-N1` | A required test is missing | NOT ASSESSABLE |
| `MAT-L1` | Every required test valid and below warning level | LOW |

A breached test wins over missing data. Interest Mode A is an earnings proxy, so it has no liquidity test and needs an approved limit.
