# Numeric checks disabled; application input context uncapped

SPEC and conflicting agent guidance were updated before implementation. Three
agents worked in parallel on W3, W4 and regression tests; the parent integrated
pipeline entrypoints, documentation and the complete offline suite. Changes remain
local and uncommitted/unpushed. No paid API call was made for this change.

## Final behavior

- W3 no longer compares numeric prose, observation values, units or scales against
  source numbers. Numeric int/float scalars normalize to public strings. Optional
  observations remain available without factual numeric verification.
- W4 no longer scans prose numbers or performs source-number subset checks. Bare
  years, amounts and percentages cannot trigger unsupported-numerical-claim repair.
- Optional numeric field structure and exact source_quote references still matter:
  null/incomplete fields or an absent quote remain explicit partial errors. This
  is evidence/JSON validation, not numeric-value/unit verification.
- No application input character/token cap in W3 grouping/construction or W4
  batching. Full original text and complete Event context are forwarded. Legacy
  chunk_size/max_chars arguments are ignored even if passed explicitly.
- Model/provider context limits still apply. Existing response-token/call-count,
  max4 Article group size, max4 Event batch size and concurrency limits remain.
- Source IDs, exact quotations/offsets, category, Claim Type, JSON structure and
  central R5 integer/range/arithmetic/rubric checks remain mandatory. Prompt-level
  instructions still prohibit invented values and unsupported company exposure.
- W4 batch + parallel remains default3 Events/batch and2 concurrent batches. Worker
  threads perform no SQLite access; valid siblings persist despite item failures.
- Public Event/RunResult stays contract6. No migration/deletion of databases,
  authentic sources, labels or prior results. Changed-policy DBs remain explicitly
  incompatible; no silent reuse. The retired source_support module was removed,
  including its fingerprint reference and now-obsolete numeric/date regex helpers.

## Verification

Locked environment sync and git diff --check passed. Complete offline suite:
82 passed in36.226s (benchmark wall time recorded in the output summary).
Focused tests:49 Event-only,16 group/consolidation and12 batch-analysis passed;
the complete suite also includes5 frozen-reference semantic-evaluation tests.

Key regressions: >16k full Article input with retired caps1/40; >32k serialized
Analysis context with max_chars1; numeric prose accepted without repair; int/float
observation strings retained; mismatched values/units retained when evidence exists;
missing exact evidence and malformed optional fields still isolated; false source
IDs/Claim Types and invalid R5 still rejected; batch concurrency, restart and
caller-thread SQLite behavior retained. Synthetic mismatches prove checks are off,
not that the mismatches are factually correct. No live semantic-quality/latency or
cost measurement was performed after this change.

Artifacts: outputs/numeric-off-unlimited-context-offline-20261101/offline.log and
summary.json. Existing Starlette/httpx TestClient deprecation warning remains.

Production files: group_first.py, analyze.py, pipeline.py; source_support.py removed.
Public model schemas unchanged. SPEC.md, AGENTS.md, README, stage/integration docs
and the three focused test modules were updated. Historical result reports were
not rewritten as new successful live evaluations.
