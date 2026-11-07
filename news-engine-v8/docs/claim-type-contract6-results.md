# Claim Type contract 6 validation ? 2026-11-01

The runtime retains Search -> Article Grouping -> Event Construction -> Scoring &
Analysis -> JSON. W3 identity remains forced same/new. No standalone Extraction,
verification Agent, confidence/evidence-status field or Event lifecycle was added.

## Contract and implementation

`claim_type` accepts exactly `actual`, `announced`, `proposed`, `forecast`.

- actual: a development reported as already occurred.
- announced: an announced future action or planned implementation.
- proposed: a proposal not yet adopted or implemented.
- forecast: a prediction or expectation about future conditions.

`models.py` defines one shared literal for public EventEntry, W3 EntryDraft and
AnalysisDraft.primary_claim_type. Both production prompts share the definitions,
require attribution and uncertainty, and instruct omission of claims that cannot
be represented faithfully. The previous prompt's unsupported-fact fallback was
removed. R5 rejects an unsupported supplied Claim Type and retains all prior caps.
Source-backed does not mean independently verified; no label is substituted when
interpretation fails. Invalid provider enum output is an explicit schema failure,
not silently reclassified or accepted. Numeric, date, source-ID and evidence-offset
validators were not weakened.

The narrowed enum is incompatible with saved v5 entries containing the removed
value. RunResult.schema_version and API version are now 6 / 6.0; Store, CLI and API
use fresh `data/news-mvp-v6.sqlite`, marked `news-engine-mvp-6`. Old databases are
refused without migration or modification. Existing clients must use the four
values. [Integration](dashboard-integration.md), [stage contracts](llm-output-contracts.md)
and [validated JSON example](../examples/event-result.json) were updated.

Semantic evaluation uses the same four classes. If supplied historical predictions
contain an obsolete label, the metric counts an unsupported prediction and retains
the original raw value. This is an evaluation column, not a production Claim Type.
No frozen source texts, labels or reference hashes were changed.

## Verification

| Check | Result |
| --- | --- |
| Locked environment | `uv sync --frozen` passed |
| New focused regressions | 8 passed, 1.434s |
| Complete offline suite | 53 passed, 15.511s test time; 17.094s benchmark wall time |
| Example JSON | RunResult validation passed |
| Provider calls / cost | 0 / $0; no paid evaluation |

Full command: `uv run --no-sync python benchmark.py --profile offline --output
outputs/claim-type-v6-offline-20261101`. Results are in that directory's offline.log
and summary.json. The first focused run exposed two test-fixture mistakes (an open
SQLite connection on Windows and an assertion expecting an omitted source in
Event-only output); those assertions/resources were corrected before the passing run.

Coverage includes all four types through construction, Analysis, storage/restart
and JSON; rejection of the removed type in all schemas and scoring; provider schema
failure without fallback; preserved disputed forecast attribution; deliberate
omission of an unrepresentable allegation; matching prompt/schema enums; unchanged
old v5 database bytes; and historical unsupported-prediction metrics. Existing
frozen authentic four-source provenance, same/new identity, multi-Event grouping,
numeric/date failures, R5, CLI/HTTP, duplicate/restart and atomicity tests also pass.

The semantic examples in new tests are synthetic deterministic fixtures, not paid
model accuracy measurements. Model interpretation of attribution and factual status
still requires real-model evaluation; this run does not establish semantic accuracy
or completeness. Starlette emits an existing httpx TestClient deprecation warning.

## Requested result cleanup

On explicit request, removed 1,348 old output files and 29 historical reports,
including obsolete assessment/result artifacts under docs/v2. Databases, original
sources, frozen labels, scripts and the pre-MVP source/patch snapshot were retained.
The new offline results above remain available. No historical metric was rewritten
as a new success; removed tracked reports remain recoverable in Git history.

A subsequently authorized paid four-source test is documented separately in
[DeepSeek smoke results](claim-type-v6-deepseek-smoke.md); it returned partial and
revealed remaining source-context, temporal-status and Analysis date-validation issues.
