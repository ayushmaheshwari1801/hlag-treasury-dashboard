# Contract 8 local-copy delivery

## Scope and location
Changes are confined to news-engine-v8, an independent work copy of the current
news-engine tree (including its pre-existing uncommitted changes). Reproducible
virtualenv/cache files were rebuilt. Original source modules, authentic corpus and
SQLite databases matched 190 checked original files against the initial snapshot.
No paid calls, push, PR update or merge. PR reference only:
https://github.com/yuguocc/agentic-treasury-dashboard/pull/11

## Architecture and contracts
W1 Search -> W2 Article Grouping -> W3 Event Classification & Consolidation ->
W4 Scoring & Analysis -> W5 JSON. W1 keeps existing search/dedup/windows. W2 uses
full text similarity .30, publication/discovery proximity 7 days and max4 Articles.
W3 partitions Articles under one dominant issue, consolidates across groups/windows,
then matches historical same/new before Python assigns persistent IDs.

Article retains article_id, original url/title/text/publisher/publication/discovery,
normalized_url and content_hash. Event retains event_id/title/category/article_ids/
summary/nullable score/why_it_matters/what_to_do. RunResult schema_version is 8.
SQLite uses a new news-engine-mvp-8 marker and article_event primary key(article_id).
No old DB migration. Established category and associations cannot change.

## Removed components
Removed EventConstructionDraft, EventDraft/EntryDraft/NumberDraft, EventEntry,
NumericObservation, EventIdentity, Evidence/SourceEvidence and Claim Type runtime
models. Removed what_changed, entry evidence, novelty relations/canonicalization,
progress timestamps, lifecycle/progression concepts and W4 entry selectors.
Rewrote existing models.py/group_first.py/events.py/storage.py/analyze.py rather than
adding another runtime. The three incompatible execution test files are preserved
unchanged in evaluation/legacy_contract7_tests. Historical reports and frozen labels
remain intact; old Claim Type metrics are isolated in evaluation-only code.

## Python enforcement
Classification validates an exact partition: no unknown, missing, repeated or
multi-Event Article IDs. Every proposed/final Event has at least one Article.
Storage atomically saves group proposals and consumes all consolidated proposals
with exact Article union. Both storage and output reject duplicate ownership.
validate_assignments counts only Events intersecting the evaluated relevant Article
set, including referenced historical Events, excluding unrelated historical Events.
Completed runs require complete assignment; partial technical failures expose pending
work and errors. Event lists are never truncated to satisfy cardinality.

## Offline results
Final full offline benchmark: **67 tests passed, zero failures, zero skips**
(including the optional Streamlit viewer). The gate ran in 24.741 seconds.

Focused and full offline regression cover 4 related->1, two issues 4->2, unrelated
4->4, multi-subject 1->1, multiple Events in one group, cross-window/group merging,
cross-run identity, unchanged-source no-rescore, failed W3/W4 restart, atomic rollback,
all categories, locks, CLI/API/viewer, R5, numerical support and uncapped requests.
See outputs/contract8-offline/benchmark/offline.log and summary.json for exact gate.

Authentic corpus replay uses FIXED GOLD-LABEL REPLIES, not a live semantic model:
72 unique Articles -> 23 Events; cardinality passed; stable cross-run assignments;
no repeated analysis for unchanged inputs. 2,556 Article pairs: 160 true merges,
2,396 true separate pairs, zero scripted false merges/splits/missing sources.
These figures validate mechanics under fixed replies, not model semantic accuracy.
Source text is unchanged and corpus integrity validation passes all 72 sources.
Replay command: python -m evaluation.offline_classification.

## R5 and limitations
R5 strict integer P/R/U 0..5, total=8P+7R+5U and nonempty explanations remain.
Caps stay disabled. W4 receives all original assigned Articles and dates, never
entries or Claim Types. Batch item validation/one repair, parallel worker limits,
main-thread SQLite writes and no input/output token caps remain. Numeric checks
normalize literal currencies/scales/percentages/units and reject unsupported prose
quantities; they cannot establish attribution, applicability or company exposure.
Spelled-out numbers and semantic numerical relationships are not fully verified.
New accepted source collections trigger analysis; exact repeated inputs are cached.
Issue boundaries, dominant-issue relevance, false merges/splits and historical
candidate retrieval remain model limitations. No paid semantic evaluation was run.
Category filtering is available in the saved-result viewer. Overall Treasury
summary uses ranked Events, themes, priorities, implications and conditional actions
without another LLM call. This is a local-only independent Git repository on
refactor/lean-news-mvp; its commit does not change the parent repository or PR11.
