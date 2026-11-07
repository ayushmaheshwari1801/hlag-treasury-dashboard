# Treasury News Engine ? contract 8 work copy

`Search -> Article Grouping -> Event Classification & Consolidation -> Scoring & Analysis -> JSON`

For the combined Treasury dashboard, use the parent workspace's `setup.cmd` and
`start.cmd`. They run the gateway and news v8 together in the parent `.venv`.
The standalone `uv` commands below are for isolated engine development only;
they are not required to install or run the combined application.

An Event is a persistent semantic label for one or more original Articles. Every
successfully classified relevant Article has exactly one primary Event. Multi-issue
Articles use their dominant issue; full source text remains available. Python and
SQLite enforce unique Article assignments and evaluated Event count <= relevant
Article count. Issue-level matching is merge-first with forced same/new decisions.

```powershell
uv sync --frozen
uv run --no-sync python benchmark.py --profile offline
uv run --no-sync python -m news_engine.pipeline --input articles.json --output result.json
uv run --no-sync python -m news_engine.api
```

This directory contains the active v8 working copy, including prior uncommitted
changes. The retired pre-v8 runtime has been removed from this workspace; historical
v8 databases, corpora and results remain preserved locally and are excluded from source ZIPs.
Use a fresh `data/news-mvp-v8.sqlite` (marker `news-engine-mvp-8`); older database
contracts and policy fingerprints are refused without migration or deletion.
Local delivery only: no remote push or PR update.

W1 preserves bounded Exa topic/geography/publication-window search and source deduplication.
W2 groups deterministically using textual similarity and a seven-day processing window,
with at most four Articles per Group. W3 classifies each full Article once, consolidates
across Groups/time windows, then matches historical Events before Python allocates IDs.
No detailed extraction, Claim Types, What Changed arrays, novelty or progression remains.

W4 receives all assigned original Articles and publication dates. It produces an Event
summary, conditional Treasury implications/actions and explained R5 scores:
`total = 8*impact + 7*relevance + 5*urgency`, each dimension a strict integer 0..5.
Hard caps remain disabled (`R5_CAPS_ENABLED=False`). Factual numerical source checks are disabled (`NUMERIC_GROUNDING_ENABLED=False`).
Source-faithful prose remains the model's responsibility; R5 arithmetic stays strict.
New Articles invalidate analysis; unchanged source collections reuse existing scores.
Batches default to three Events/two workers (limits four/eight), bind by Event ID and
automatically repair only failed Events up to twice. SQLite writes remain on the calling thread.

The public `RunResult` has schema_version 8, events, sources, errors, metrics and a
readable deterministic overall summary. Filter Events by regulatory, macroeconomics
or industry. The saved-output viewer is read-only and makes no provider calls:

```powershell
uv sync --frozen --extra viewer
uv run --no-sync streamlit run streamlit_app.py --server.address 127.0.0.1
```

Search/model credentials are read from local `.env`; the HTTP service requires
`NEWS_API_TOKEN`. Model requests have no application input truncation or output-token
cap; provider capacity/completion failures remain explicit. Offline tests use scripted
replies and frozen authentic text. Paid evaluations require explicit authorization.

[Specification](SPEC.md), [integration](docs/dashboard-integration.md),
[model contracts](docs/llm-output-contracts.md), [example](examples/event-result.json).

Automatic W4 recovery stops after success or two single-Event repair calls. With numerical source checks disabled,
number mismatches do not cause retries or force qualitative narrative. Successful siblings and
existing scores are reused; Search and classification are not repeated. Provider,
authentication, credit and transport failures stop immediately. Exhausted Events
remain explicitly unscored/partial. Actual calls, tokens and costs are recorded.


## ID-constrained parallel execution

The current classification output is a required per-Article assignment object, not model-generated `article_ids` lists. Local issue handles and candidate Event IDs are constrained by runtime enums. Duplicate JSON keys, missing/foreign IDs and duplicate/undefined issue references are rejected before persistence. Full source text and the public contract-8 Article/Event result stay unchanged.

W3 uses the existing `workers` setting (default 4, maximum 8) for classification, wave-based local consolidation and historical matching in separate bounded phases. New sibling issues are reconciled before global consolidation completes; historical decisions see a fixed pre-run snapshot and the caller merges into the latest state. SQLite remains on the caller thread. W4 remains 2 workers with 3 Events per batch. Unique-heavy consolidation may need extra reconciliation calls, so actual speedup depends on the input and provider.

Terminal ID errors include exact diagnostic lists in saved `errors[].message`. Provider/schema failures and successfully repaired semantic failures are also retained in `metrics.id_validation_errors`; recorded validation audits do not add provider calls or billing receipts. The dashboard exposes terminal errors under an expandable collection-errors section.

Because the classification policy changes, the default HTTP/CLI/dashboard database is `data/news-mvp-v8-id-constrained.sqlite`. Old databases and exported runs are preserved, never migrated or relabelled. Override the dashboard path with `NEWS_V8_DATABASE` only to a compatible database. Saved news remains visible while the new database is empty. No automatic paid replay of old Articles runs during startup.


## Ten-Article default for refresh and rerun

HTTP runs, dashboard Refresh news and CLI now default to `max_articles=10` (`--max-articles 10`). The limit counts selected unique new and resumed Articles together; it is not a ten-Event limit. Discovery may return and preserve more originals, but only the selected batch enters processing. Unfinished saved proposals and unclassified Articles take priority over cached input. Atomic saved units are never split to fit: an unscored Event or proposal larger than the budget remains pending, with IDs/counts in `metrics.budget_blocked_units`; explicitly raise the budget to resume it. `metrics.articles_selected` and `articles_deferred` report the selected batch and remaining unselected backlog.

A selected new Article may match a historical Event; all of that Event's existing original evidence remains intact for analysis. `metrics.analysis_context_articles` therefore can exceed ten even when only one new Article is selected. This setting does not truncate historical evidence. Explicit internal Python offline replays retain `max_articles=None` for full-corpus contract verification. Changing defaults does not start or retry a paid run.


## Unused local issue definitions

If valid classification output defines local P-handles that no Article references, drop those empty definitions and record `unused_issue_drafts_removed` in `metrics.warnings`. Article assignments stay unchanged; a warning-only run is completed, including on cached replay. Duplicate definitions, missing/foreign Article IDs, duplicate JSON assignment keys and undefined issue references still fail. Warnings and errors have separate expandable sections in News Tracker. No repair call is needed for this cleanup.

The current identity policy defaults to `data/news-mvp-v8-issue-identity-v2.sqlite` for HTTP/CLI/dashboard execution; prior databases and saved exports are retained unchanged. The per-run ten-Article default remains. Old saved errors are historical records, not retroactively converted warnings. Matching now uses both sides' full original Articles to separate independent instruments and preserve the identity of announcement/activation updates. Invalid classifications receive at most one validation repair; provider failures are not retried.
