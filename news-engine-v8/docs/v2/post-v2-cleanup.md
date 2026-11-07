> Historical reference only. Current runtime, commands and acceptance are defined in [SPEC.md](../../SPEC.md).

# Post-V2 legacy cleanup

This review is based on `refactor/major-change-v2` at `914b491` and lives on
`cleanup/post-v2-legacy`. It is a cleanup review, not deployment approval. The
previous 72-source extraction completed, but independent MajorChange recall and
semantic accuracy remain unverified; its recorded quality flags still apply.

## Inventory before deletion

The [pre-cleanup file manifest](post-v2-cleanup-manifest.json), committed before
deletion, classifies all 313 tracked repository files, records SHA-256 hashes and
lists static imports, literal dynamic imports and retired-contract references.
It covers production, schemas, SQL-bearing modules, prompts, API/viewer consumers,
evaluation, tests, package configuration, documentation and CI. The production
dependency gate also imports every current production module.

| File or group | Classification and disposition |
| --- | --- |
| `src/news_agent/live_logs.py` | Deleted: no callers; reads retired W2/W3 checkpoint fields. The viewer reads V2 saved monitoring exports. |
| `src/news_agent/monitoring.py` | Retained atomic JSON writer used by evaluation; deleted unused `export_monitoring()` and its Run import. |
| `src/news_agent/run.py` | Retained database/provider maintenance; removed duplicate `--collect`, `--config`, `--outputs` execution options. Collection uses `news_agent.orchestrator collect`. Read-only checks now require the marked V2 database. |
| `scripts/check_major_change_dependencies.py` | Retained and strengthened with static/dynamic local-import audit and retired log-module rejection. |
| `scripts/check_major_change_workflow.py` | Retained; added maintenance safety and real TCP HTTP startup/auth/restart checks. Streamlit AppTest gets an explicit 15-second startup timeout, retaining all output assertions. |
| `.github/workflows/news-engine.yml` | Retained; cleanup branch runs offline checks, paid runs require manual dispatch, Gemini is the live default. |
| `src/news_agent/v2/*` | Retained unchanged: sole MajorChange, Event, EventScore and Report implementation. No namespace rename or policy change. |
| Other production modules and prompts | Retained: source collection, numeric/citation checks, Watch metadata, retrieval, LLM transport, R5, checkpoints and service infrastructure. |
| `/api/v1/jobs` control aliases | Retained by rewrite section 5; one V2 executor, no Fact adapter and no V1 result route. |
| Evaluation modules, corpus, labels, fixtures and historical docs | Retained. Archived result readers are evaluation-only; Fact terminology in original labels is not a runtime fallback. |

The V1 fact extraction/selection/conversion/catalog modules and old extraction
prompts were already absent at this branch's starting commit. Their absence is
verified rather than deleting shared utilities or historical labels bearing old
names. No additional archives were created or moved; retired source remains in Git.

## Verification and limits

The former measured-results report (removed on request) record
the final full offline pass (8/8 commands, including 18 workflow tests), the
43-module production import gate and 188 unchanged protected files. Both offline
suites passed without provider calls. Full corpus validation still reports 72
sources; the existing 57-cohort service runner and gold labels are unchanged.

The workflow suite covers source evidence, independent occurrences, Event identity,
unverified claims, actual completion, R5/version binding, historical score cutoffs,
deadline revisions, report citations, viewer output, HTTP job idempotency and
resume without repeating completed extraction. The TCP smoke additionally starts
the real Uvicorn CLI twice against a temporary database and checks authentication,
V2 routes and invalid-request rejection. It does not submit paid work over HTTP.

The first full offline run passed contracts but hit Streamlit's default three-second
AppTest timeout. That failed run is preserved at
`outputs/post-v2-cleanup/offline-20261101/`. The isolated workflow rerun passed;
the final full-suite result is recorded in the final audit results.

The live Gemini smoke used four authentic sources in two historical cohorts,
reasoning off and a fresh database under
`outputs/post-v2-cleanup/live-smoke-20261101/`. It completed both cohorts with
seven accepted Changes, zero missing-source Event diagnostics, zero false merges
and zero false split/missing pairs. One expected same-Event pair was recovered.
There were 37 model calls, 140,064 prompt tokens, 9,466 completion tokens,
101.094 seconds elapsed and USD 0.09843 provider-reported cost. This is a direct
workflow/provider smoke, not a live HTTP job or Exa-search benchmark.

No 72-source paid rerun or throughput improvement is claimed. MajorChange recall
is unknown; recorded offline replies and four live sources do not certify it.
Frozen V2 extraction, review, scoring and report code/prompts are hash-checked
against the pre-cleanup inventory, as are frozen corpora and gold labels.

## Data safety

No existing SQLite database, report, artifact or user output is deleted, rewritten
or relocated. New tests use temporary directories; new evaluation evidence lives
only under `outputs/post-v2-cleanup/`. Credentials and local databases are excluded
from commits. Existing baseline/failure artifacts remain local and unchanged.
The marked V2 storage/schema-7 requirement and historical rejection tests remain.

Reproduce the audit without provider calls:

```sh
uv run --no-sync python scripts/audit_legacy_dependencies.py --baseline docs/v2/post-v2-cleanup-manifest.json --output outputs/post-v2-cleanup/dependency-audit.json
uv run --no-sync python benchmark.py --profile offline
uv run --no-sync python -m evaluation.service_benchmark --selection full --validate-only
```
