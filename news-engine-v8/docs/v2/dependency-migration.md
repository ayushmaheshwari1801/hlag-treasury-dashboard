> Historical reference only. Current runtime, commands and acceptance are defined in [SPEC.md](../../SPEC.md).

# Complete legacy dependency migration

The additional user requirement supersedes the temporary dual-entry-point stage:
this branch now has one executable pipeline. V1 remains reproducible from Git at
`c0fe0b12257349bf5051bd1159ab63d29ce4ea69`, without rewriting its databases.
Changing this branch does not deploy it over an existing service.

## Audit and classification

`dependency-audit.json` records the pre-migration symbol references with path,
line, classification and required action. `complete-dependency-audit.txt` is the
raw search inventory. The scan covered runtime code, schemas, persistence,
prompts, exports, Streamlit, evaluation scripts and frozen fixtures. A separate
parent-repository scan found documentation and the instruction-only host skill,
but no additional frontend application or consumer of the retired JSON fields.

| Classification | Modules/contracts | Disposition |
| --- | --- | --- |
| Must migrate | Fact/Candidate models, extraction/review, source projection, Event references, scoring evidence, report catalog | Replaced by MajorChange, change_ids, ChangeRef and Change catalog |
| Must migrate | Store schema guards, Article summary, W1 yield reconciliation, execution settings, CLI, HTTP results, Streamlit | Canonical schema 7, Change summary schema 1, source schema 5, result schema 2, report schema 4 |
| Reusable infrastructure | Exa/LLM clients, authentication, job persistence/thread worker, run/stage checkpoints, SQLite transactions, URL revisions, numeric checks, source segmentation, keyword retrieval, R5 rubric | Retained; changed only affected contracts, date-format guidance, latency receipts and Windows portability |
| Historical/evaluation only | Authentic source corpus, Event gold, earlier synthetic labels, archived report metrics | Preserved and isolated; never used as model inputs |
| Historical execution only | Retired Fact-specific runtime modules, prompts and execution tests | Removed from current runtime; originals and passing baseline results remain in Git/history |
| Separate host mode | `skills/treasury-intelligence/` | Instruction-only execution by a host agent; no program/API imports or retired JSON field dependencies |

`retired-runtime.json` and `retired-execution-tests.json` identify retired files
and their recovery baseline. Tests were retired because they instantiate removed
contracts, not because they failed: the existing offline profile passed before
the canonical cutover. Shared transport/JSON/corpus checks remain active. New
contracts test the replacement behavior, including completed-work reuse,
partial-chunk recovery, immutable history, actual completion and score cutoffs.

## Production dependency invariant

The [post-V2 cleanup review](post-v2-cleanup.md) records the subsequent file-level
inventory, removals, retained API aliases, data safety and validation scope.

```sh
uv run python scripts/check_major_change_dependencies.py
```

This imports every production module and scans source/prompts/viewer for
ExtractedFact, FactDraft, fact_ids, major_fact_ids, fact_refs, fact_catalog,
CanonicalFactRef, ArticleAnalysis and the retired ExtractionResult. It also
rejects legacy/unified extraction configuration. No production fallback remains.
The temporary `news_agent.v2` package is now the sole core, not a version switch.

`source_chunks.py` is contract-independent infrastructure extracted from the old
planner; it imports neither retired extraction models nor their executor.
`event_settings.py` separates reusable retrieval settings from retired Fact refs.
All active source summaries project accepted Change IDs; source checkpoints own
the evidence. Event, score and report references hydrate only accepted exact
revision/policy/range bindings. Source revisions suspend superseded current
evidence without changing historical snapshots.

The HTTP worker has one executor. `/api/v1/jobs` control aliases point to it;
incompatible saved result bodies are exposed only through the V2 result contract
and rejected if their source schema is retired. Database versions 5/6 are rejected
before schema changes. No compatibility adapter or destructive backfill exists.

## Acceptance limits

Runnable code, import checks and recorded-provider recovery tests do not prove
material-change recall. The authentic corpus and Event labels remain unchanged.
Live failures and unattempted sources remain visible in reports; independently
adjudicated MajorChange labels and a successful matched V1/V2 comparison remain
quality work. Do not award performance wins to runs with critical omissions.
