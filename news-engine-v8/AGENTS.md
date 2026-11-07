# News Engine v8 work-copy guide

Read/update SPEC.md before behavior changes. W1 Search -> W2 Article Grouping ->
W3 Event Classification & Consolidation -> W4 Scoring & Analysis -> W5 JSON.
Article and Event only. Each relevant Article has exactly one primary Event.
Python and SQLite enforce unique immutable article_id -> event_id and evaluated-set
Event count <= relevant Article count. No extraction, entries, claim types, novelty,
lifecycle or progression. Preserve full Articles and issue-level merge-first identity.
Binary same/new only. Consolidate all local proposals before historical matching.
Fresh marked v8 database, never migrate/delete original databases, corpora or reports.
Preserve locks, atomic pending work, restart/cache and explicit technical failures.
W4 analyzes original assigned Articles; R5 strict 0..5 integers and 8P+7R+5U arithmetic.
R5_CAPS_ENABLED stays False. NUMERIC_GROUNDING_ENABLED=False disables factual-number
source matching; preserve strict R5 range/arithmetic and all structural checks.
Batches default3/max4, workers default2/max8; per-Event validation, at most two automatic repairs,
no SQLite in workers. No application input length or output-token caps.
English docs. Full offline benchmark, no paid calls. This directory is an isolated
work copy. Local commit only; do not push, update PR11 or merge.

W4 automatically repairs validation failures only, stops after success or two
single-Event attempts. Numeric mismatch cannot trigger repair or forced qualitative
prose while numerical grounding is disabled. Never retry provider/auth/credit/transport failures or redo successful work.
Keep strict validation, full originals, explicit exhaustion and actual cost metrics.
