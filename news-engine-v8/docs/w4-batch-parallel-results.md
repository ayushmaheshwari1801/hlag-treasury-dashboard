# W4 batch + parallel offline results

Implementation remains local: no commit, push or paid model evaluation in this task.
SPEC was updated before implementation. Five stages and public contract6 remain.

## Runtime

W3 consolidation finishes before W4. Only changed/unscored canonical Events enter
W4. Defaults:3 Events per batch,2 concurrent batches. Python, CLI and HTTP expose
analysis_batch_size1..4 and analysis_workers1..8. Serialized input context is capped
at32000 characters; oversize Events remain stored/unscored with an explicit error.

Input: shared reference_time/treasury_context and events containing current_event,
source_times and allowed_values. Output: results[{event_id,analysis:AnalysisDraft}].
Match exact supplied IDs, never array positions. Unknown IDs are explicit errors;
missing/duplicate IDs invalidate only the affected Event's result. Singleton batches
use the same path. The former independent analyze_event runtime was removed.

The provider receives a strict nested Analysis schema. Pydantic SkipValidation
only defers nested parsing to validate_analysis per item; it does not accept an
invalid score/evidence result. Every accepted item passes AnalysisDraft, source/
exact quote/Claim Type, non-temporal numeric checks and the existing R5 constraints.
Valid siblings are kept. Each failed Event has at most one repair via the existing
single-item Analysis schema. A whole malformed response may require one repair for
each affected Event. Provider failures add no retry loop. Output budget:2048 tokens
per Event, at most8192 per batch; repair2048.

Existing ThreadPoolExecutor infrastructure runs batches concurrently. Workers do
model requests and in-memory validation only. The caller processes completed
futures, writes SQLite and updates event_map. Final R5 ranking is unchanged and
independent of output/completion order. Metrics expose analysis_batches, configured
batch size/workers, analyze_batch initial calls and analyze repair calls.

## Verification

- Focused batch suite:11 passed in3.718s.
- Full offline suite:80 passed in28.961s.
- Locked environment sync passed; git diff --check passed.
- No provider calls or paid cost; speed/semantic-quality claims are not measured.

Tests cover reversed output mapping; malformed nested analysis; independent R5,
source, numeric and Claim Type failures; missing/duplicate/unknown IDs; malformed
whole response repair bounds; provider failure without retry; context splitting
and oversized Event retention; partial score persistence and restart; strict
provider schema and6144 output budget for3 Events; Python/HTTP parameter bounds;
CLI/HTTP setting forwarding; duplicate/no-rescore; and existing provenance/R5 tests.

The concurrency test uses a two-party Barrier rather than latency guesses. Six
unscored Events produce two batches of3; both requests overlap, observed maximum
active batches2, exactly two batch calls, six successful scores. A patched writer
asserts all six SQLite writes occur on the caller thread. Batch output order is
explicitly reversed in a separate test.

The first focused run had one test assertion incorrectly expecting a $defs reference;
Pydantic inlines the deferred Analysis schema under BatchAnalysisItem.analysis.
The assertion was corrected to inspect the actual provider schema; no production
validation was weakened. Existing Starlette/httpx deprecation warning remains.

Logs: outputs/w4-batch-parallel-offline-20261101/offline.log and summary.json.

Changed modules: analyze.py (batch schema/context/validation/repair), pipeline.py
(scheduling/main-thread writes/settings), clients.py (bounded batch output budget),
api.py (additive settings); tests and authoritative/integration docs were updated.

Remaining limits: one corrupt top-level response can affect its entire batch;
Event context above32000 characters needs a budget policy adjustment; cross-Event
semantic mixing, actual latency and cost require separately authorized model tests.
No paid smoke or provider-native asynchronous Batch service was used.
