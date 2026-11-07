# Local construction, global consolidation and temporal-check removal

Implementation remains local and uncommitted/unpushed at the user's instruction.

## Production behavior

Search -> Article Grouping -> W3 Event Construction & Consolidation -> Scoring &
Analysis -> JSON. W3 first constructs drafts once per bounded group of full source
Articles, then resolves local draft equivalence across groups, then associates
canonical drafts with historical/current SQLite Events using binary same/new.
Temporary D-handles are not persistent IDs; Python allocates E-IDs only after
identity resolution. W4 runs after consolidation and canonical persistence.

Each local Event declares source_ids and each embedded entry declares source_id.
Evidence is validated against that entry's original Article, not a current single
Article. Exact citations, numeric value/unit checks, categories and Claim Types
remain. Groups contain at most4 Articles and16000 combined source-text characters
by default. Oversized singletons fail explicitly without a model call or truncation;
a higher chunk_size can retry failed work without discarding accepted sources.

Group drafts/source states/diagnostics commit together. Canonical Event writes
consume every contributing draft receipt in the same transaction. Rollback and
restart tests preserve multi-source work and stable historical IDs. Identical
normalized descriptions/types/numeric observations retain one entry with additional
supporting citations. Distinct accepted entries are preserved regardless of a
model's duplicate/corroboration novelty label. Different paraphrases can still
remain as repeated entries. Scoring can cite primary or supporting evidence while
inheriting the canonical entry's Claim Type.

No Event lifecycle, standalone Extraction, new Agent framework, mandatory reviewer
or persistence table was added. Public Event/RunResult fields remain contract6;
existing old-policy DBs are explicitly refused rather than silently migrated.

At the user's request, W3 no longer source-validates or clears occurrence_at.
Calendar/clock syntax is excluded from W3/W4 numeric prose checks on BOTH sides;
a source's date digits cannot justify an invented financial amount. Pydantic date
field format parsing remains. Financial quantities/units, exact evidence and source
IDs are still checked. Generic recognition covers ISO/numeric/English calendar
forms, clock forms and explicit calendar-year/quarter context; it is not a semantic
date verifier. Source-backed duration-valued numeric observations retain normal
value/unit checking.

## Offline verification

- Group/global-consolidation tests:16 passed (including4 temporal/financial cases).
- Complete suite:69 passed,15.135s test time;15.719s benchmark wall time.
- CLI/HTTP, four Claim Types, source/numeric validation, categories, duplicate/no-
  rescore, transactional rollback, restart/history and scoring tests pass.
- Frozen authentic PPP source provenance and distinct issue boundaries pass with
  scripted providers. This is not a paid-model semantic accuracy measurement.
- First full W3 run had one fixture NameError after an import cleanup; the missing
  fixture import was restored before final validation.
- `git diff --check` passed. Existing Starlette/httpx deprecation warning remains.

Final offline logs: outputs/no-date-time-validation-offline-20261101.

## Authorized API test BEFORE temporal-check removal

Model deepseek/deepseek-v4.1-flash, four authentic PPP sources in two chronological
cohorts, fresh DB. Search/Judge/full72 replay not run. Test occurred before upload.

Status partial: first cohort completed; second partial. All4 sources represented;
3 canonical Events,2 scored; no pending drafts. Construction calls2 (one per group),
identity calls5 (local2,historical3), Analysis calls5 including one repair; total12.
Time23.765s; cost$0.00606255048; tokens32536 input/4299 output. Invalid-source drafts0,
discarded entries0, invalid observations0; accepted evidence references checked10,
invalid0. Frozen source-pair grouping:1 same pair,5 distinct pairs, no false merges
or splits. Facility announcement/operation share an ID; capital and Board rules
remain separate. Pair metrics do not establish complete semantic correctness.

Failures were two optional occurrence_at clearances (absolute dates inferred from
Thursday/Friday plus publication dates), and Board Analysis rejecting17 from the
prose date2020-04-17 while its ISO metadata date's17T was not parsed as a number.
The original API artifacts remain unchanged:
outputs/local-global-w3-deepseek-20261101 and its primary_replies/report/receipts.
Offline reference metrics: outputs/local-global-w3-semantic-20261101. They report
status5/9 including anchor misses,5/6 matched-only, anchor coverage7/11 and category
agreement7/7 matched references. Anchor matching misses paraphrases and is not
independently adjudicated semantic recall. Board still mixes future implementation
with actual announcement and omits explicit independent SBA clarification; some
scoring prose asserts no company effect too categorically. These are not fixed by
removing temporal validation.

## Saved-response replay AFTER temporal-check removal

The same saved responses were replayed offline through current code in a fresh DB.
Both cohorts completed with zero processing errors; all3 Events scored, no Analysis
repair. Board occurrence_at is retained. The facility keeps its original historical
identity (whose date was null); this is distinct from clearing a local draft's date.
This validates the changed acceptance logic, not new live model output/accuracy.
Provider calls0, cost$0. Artifacts:
outputs/no-date-time-validation-recorded-replay-fixed-20261101.

An initial replay harness keyed Analysis only by Event ID and accidentally replaced
an earlier facility response with its later snapshot, causing a harness KeyError.
The corrected replay key includes the source set. Both attempts remain separate
artifacts; the initial failure is not rewritten. No further paid API call was made.


## Subsequently authorized single paid retry after temporal-check removal

A single four-source evaluation was explicitly authorized and executed using
DeepSeek v4.1 Flash. Both cohorts completed with zero processing errors, all4
sources represented,2 final Events and2 scored Events (R5 totals20 and20).
Date/numeric errors0, invalid source drafts0, discarded entries/observations0.
Calls9: construction2, identity4, Analysis3, with no repair. Time18.796s;
cost$0.004668966428; input24762/output3659 tokens. Evidence references checked14,
invalid0. No Search, Judge,72-source replay or automatic paid retry was run.

Semantic grouping did regress in this stochastic run: the local binary matcher
explicitly acknowledged that the facility and capital rule were distinct actions
but merged them as a shared PPP implementation issue. Frozen pair metrics report
2 false merges,1 true same pair,3 true distinct pairs, no false splits/missing
sources. Board future implementation is still presented in actual entries. The
completed technical status is not full semantic acceptance. No code was uploaded.
Artifacts: outputs/no-date-time-validation-deepseek-20261101, including original
contexts/responses, both cohort outputs, report, receipts and fresh database.
