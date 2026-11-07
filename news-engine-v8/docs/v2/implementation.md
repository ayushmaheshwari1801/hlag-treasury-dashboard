> Historical reference only. Current runtime, commands and acceptance are defined in [SPEC.md](../../SPEC.md).

# Current canonical state

The reasoning-aware performance amendment is implemented in small commits
`bc31c39` (receipts), `709d830` (stage policy), `3a19c1a` (owner-thread checkpoint
concurrency), `915b17e` (extraction matrix), and `e9d8f1f` (supplied-ID constraints
and fair failure denominators). See the former performance report (removed on request).
The complete offline benchmark passes; real-provider quality/performance acceptance
is still open. The first matrix attempt is retained as failed: four workers,
reasoning off, 157.313 seconds, 51/72 complete; subsequent cells were interrupted
to correct serial failure handling. It is not a valid comparative speed claim.

The additional complete-dependency requirement replaces the earlier temporary
opt-in stage. There is now one Major Change runtime; retired Fact modules/prompts
and their execution tests are removed from the active tree. Shared infrastructure,
authentic datasets and pure historical metrics remain. SQLite schema 7 rejects
older databases without changes. All production modules import and the retired
symbol gate passes. The notes below are chronological implementation history,
not remaining requirements; current quality limitations are recorded separately.

## DeepSeek-only replay (2026-10-31)

The user requested V2-only execution after clearing earlier generated rewrite
outputs. Original V1 data and frozen authentic evaluation inputs remain intact.
All new model roles use `deepseek/deepseek-v4-flash`; there is no V1 replay.

`outputs/v2-deepseek/smoke-r3` completed both workflow cohorts and preserved one
facility identity across announcement and operation, with two separately scored
Event versions and two reports. It nevertheless **failed quality diagnostics**:
two of four sources had no extracted Changes (new bank rules were filtered as
company-irrelevant). The run used 21 calls, 51,916 prompt tokens, 37,283 completion
tokens, 527.828 seconds and provider-reported USD 0.0180173082. These are replay
measurements, not a performance improvement claim or independent recall estimate.

Extraction and coverage review now explicitly preserve material domain changes
before downstream company-relevance scoring, which can be zero. The full offline
benchmark passed with this correction in `outputs/v2-deepseek/offline-r4`.
The fresh 72-source, reasoning-enabled live replay was started in
`outputs/v2-deepseek/full-r4`; inspect its report and receipts for completion and
failures. Its quality acceptance remains open until execution and review finish.

# Major Change V2 implementation record

## Phase 0: baseline and architecture decision

Baseline: `main` at `c0fe0b12257349bf5051bd1159ab63d29ce4ea69`.
Implementation branch: `refactor/major-change-v2`.

V2 is a breaking extraction contract. A MajorChange owns both the material
development and its occurrence hints; generic facts and independent candidates
are no longer its intermediate representation. V1 remains the active runtime
until equivalent recovery, quality and downstream checks pass. Historical
accepted rows are never converted in place.

V2 must use a fresh database and `outputs/v2/`. No V2 runtime has been enabled
yet. Database isolation must be enforced before adding its first persistence path.

`baseline-manifest.json` freezes corpus files, gold files, prompts, configuration
where present, and dependency lockfile using SHA-256. Preserve these original
files; separate MajorChange labels must not replace Event labels.

`dependency-inventory.txt` records references with source line numbers. Entries
under `src/` and `streamlit_app.py` are runtime dependencies; `scripts/`, `tests/`
and `evaluation/` are verification dependencies, including recorded-provider
fixtures. This inventory is a baseline, not a generated migration adapter.

## Contract map

| V1 | V2 replacement | Consumers |
| --- | --- | --- |
| FactDraft / ExtractedFact | MajorChangeDraft / MajorChange | extraction, review, storage |
| NumericObservation | NumericChange with nullable levels and delta | validation, exports, viewer |
| CandidateDraft | MajorChange.event_hint | retrieval and association |
| fact_ids / major_fact_ids | change IDs | article projection and exports |
| fact_refs | ChangeRef with revision, policy and disjoint ranges | developments, scoring, reports |
| canonical_context | bounded accepted Change catalog | score/report prompts and review |
| ArticleAnalysis | versioned MajorChangeExtraction | checkpoints, API, exports |
| Event-version scoring | same R5 rules, Change evidence | score tasks and reports |

No performance or real-model quality result is established by these artifacts.
Live V1/V2 comparison has not run. Provider credentials and local environment
secrets are excluded from the manifest.

## Baseline execution and Phase 1 progress

Installed locked evaluation extras with `uv sync --frozen --extra evaluation`.
Windows defaults to GBK here; run the benchmark with `PYTHONUTF8=1`.
The unchanged baseline run at `outputs/benchmarks/20261031T085023648644Z`
has existing failures, including `check_real_corpus.py`: Windows path separators
make the generated inventory differ from its frozen manifest. Do not rewrite
the corpus to mask this failure. The first attempt without UTF-8 also failed
while reading prompts. Full baseline success has not been established.

Phase 1 is in progress: strict V2 models, deterministic policy-bound Change IDs,
revision/range validation and disjoint citation hydration are implemented.
`python scripts/check_major_change_v2.py` passes six offline boundary tests.
These helpers do not establish semantic approval or numeric evidence correctness.
Extraction/review prompts, arithmetic/evidence validation, chunk persistence and
recorded-provider end-to-end extraction remain required before Phase 1 exits.
No V2 database or production runtime switch has been introduced.

### Phase 1 extraction increment

Added change-only extraction/review prompts, bounded targeted repair preserving
accepted siblings within an attempt, exact Decimal/unit checks using the shared
evidence implementation, and source-chunk processing using the existing planner.
`ChangeStore` requires a new database or an explicitly marked V2 database and
uses the existing Store transaction infrastructure. Accepted complete chunks are
immutable and replay after reopening without provider calls. V1 database opening
through this entry point is rejected before mutation.

Ten checks pass with `.venv/Scripts/python.exe scripts/check_major_change_v2.py`,
including numeric arithmetic/sign evidence and complete-chunk restart/replay.
The isolated database is created only when explicitly constructing ChangeStore;
no production database was created or changed during implementation.

Phase 1 has not exited: partial accepted siblings are currently retained within
bounded repair only, not durably across a failed chunk attempt. Durable partial
repair, failure recovery with material Changes, qualification and long-source
recorded-provider cases remain required. Code-validation errors currently fail
the chunk rather than entering targeted semantic repair. These are explicit
implementation gaps, not accepted production limitations.

The unchanged UTF-8 baseline completed with failures: corpus manifest path
portability and HTTP API import of Unix-only `fcntl` on Windows. Results remain
under `outputs/benchmarks/20261031T085023648644Z`; no corpus labels were changed.

### Recovery and Phase 2 foundation (historical incremental entry)

Partial accepted Changes now checkpoint after semantic review and survive a
restart. Recovery includes accepted siblings in the targeted repair request;
checkpoint updates reject removal or modification of accepted siblings. Completed
chunks continue to use immutable checkpoints. The boundary suite now exercises a
material status Change through partial persistence, reopen and extraction repair,
including rejection of dropped siblings.

Phase 2 has started with ChangeRef and ChangeCandidate contracts, direct candidate
construction from accepted MajorChange.event_hint and reuse of bounded keyword/time
retrieval. Incomplete extraction cannot construct Event candidates. Disjoint
citations remain separate. No Fact IDs are generated by these V2 interfaces.

Neither phase is declared complete: Phase 1 still needs broader material-response,
provider-failure and long-source regression cases and code-error repair handling.
Phase 2 still needs association decisions, durable Event developments, lifecycle
and correction review, replay accounting and atomic score-queue integration.
The production Event ledger and execution switches remain unchanged.
