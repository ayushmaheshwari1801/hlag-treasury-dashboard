# Treasury News Engine Specification ? contract 8

Sole authority for this work copy. Supersedes contract 7 construction and progression.
Documentation is English. Original databases, frozen corpora and historical results are preserved.

## Architecture
W1 Search -> W2 Article Grouping -> W3 Event Classification & Consolidation -> W4 Scoring & Analysis -> W5 JSON.
Two business entities: immutable original Article and persistent semantic Event cluster.
Many Articles -> one Event. Each successfully classified relevant Article has exactly
one primary Event; every Event has Articles. Classify multi-issue Articles by the dominant
issue and preserve all original text. Never create secondary Events from one Article.
No Extraction/Fact/MajorChange/Claim Type/What Changed/progression/lifecycle layer.

## W1 and W2
Preserve bounded Exa Topic x Geography x Time planning, incremental deduplication,
configurable publication filters and existing 48-hour search/one-day overlap behavior.
Scopes: Global, North America, Europe, Asia-Pacific, Middle East, Latin America, Africa;
specific countries, ports, trade lanes and chokepoints are allowed. Geography proves no exposure.
W2 is deterministic lexical similarity plus publication proximity (7 days, threshold .30,
max 4 Articles). published_at falls back to discovered_at for processing only. Cover every
Article exactly once, allow singletons, use temporary Group IDs, order input deterministically.
Time windows never constrain cross-group W3 consolidation.

## W3
One classification call per full bounded group returns complete, local issue definitions
and an assignment object with exactly one required key for every supplied Article ID.
Each value is one allowed temporary issue handle or null for a genuinely irrelevant Article.
The runtime schema constrains Article keys and issue handles; Python builds Event article_ids
from this unique partition. Reject missing/duplicate/foreign Article keys, duplicate issue handles and invalid references.
Remove only valid but unreferenced issue definitions with a recorded warning; never drop or reassign Articles.
Collect persisted local proposals; consolidate across all groups with temporary D handles,
then resolve historical identity before allocating Python-owned E IDs. Binary same/new only;
same selects one supplied ID, new none, no candidates creates new. Merge-first issue identity:
facility announcement/launch same, provisions of one capital rule same, related SBA/Board
eligibility preferably same; independent facility and capital rule separate. Shared industry,
country, regulator or program alone never justifies merging. No novelty/relation labels.
Identity is the specific instrument/decision and its operative scope, not its umbrella
program. Independent instruments MUST remain separate even when one release mentions
the other as background. Announcement and activation of the same instrument MUST retain
identity. Classification, local consolidation and historical matching share this rule.
Matching receives the full original Articles of both proposals and candidates; summaries
are navigation aids, not sufficient evidence to merge. Missing referenced sources fail
explicitly. Existing immutable assignments are not repaired in place; changed policy
requires a fresh database and preserves the prior database and saved exports.
An invalid/incomplete classification may receive one validation-only repair using
the same full originals and exact ID constraints. Stop after success or that repair;
provider/authentication/credit/transport failures are never retried. Exhaustion keeps
the Articles pending with an explicit error; a repair never silently drops them.
Technical failures retain pending proposals. Save group work atomically; consume all contributing
proposals with final associations atomically. SQLite article_id -> event_id is unique and immutable.
Established historical categories normally stay unchanged. Category enum: regulatory,
macroeconomics, industry. Never truncate Events to satisfy cardinality.

## Contracts and persistence
Article: article_id, url, title, full text, publisher, published_at, discovered_at,
normalized_url, content_hash. Event: event_id, title, category, article_ids, summary,
score (nullable), why_it_matters, what_to_do. No entry evidence or identity/progression fields.
Fresh marked news-engine-mvp-8 database, default data/news-mvp-v8-issue-identity-v2.sqlite. Refuse old contracts
without modification. Stable IDs, locks, policy fingerprints, run cache, restart and explicit
errors remain. Technical pending/classification metadata is not a business entity.
New Article associations invalidate analysis; unchanged source collections are not rescored.

## W4 and R5
Analyze final Event with ALL assigned original Articles/publication dates, Treasury context
and existing R5 rubric. Return source-grounded summary, why_it_matters, what_to_do,
strict integer P/R/U 0..5 and nonempty dimension explanations; total=8P+7R+5U.
R5_CAPS_ENABLED remains False; caps/cross-dimensional/action restrictions disabled, anchors
advisory. Retain applicable flag-controlled basis/cross-dimension/action caps; remove Claim
Type-dependent caps/selectors because Claim Types no longer exist. Never invent company
exposure or financial effects. Recommendations are concise and conditional.
NUMERIC_GROUNDING_ENABLED in models.py is False: no deterministic source matching
of narrative amounts, currencies, units, signs, dates or computed quantities runs.
The model must still use original Articles faithfully; no extraction or review Agent.
Default 3 Events/batch (max4), 2 parallel batches (max8). Bind by exact Event ID, validate
items independently, at most two automatic single-Event repairs per failed Event, keep valid siblings. Workers never touch SQLite.
No application context truncation/length rejection or output-token caps in any LLM request.
Keep provider completion checks, call/query/concurrency budgets and usage/cost metrics.

## W5 and validation
RunResult schema_version=8, run_id, status, events, sources, errors, metrics, overall_summary.
Overall summary is deterministic readable synthesis of final ranked Events, priorities,
Treasury implications and conditional actions; no additional mandatory LLM call.
Support category filtering. Ranking total/relevance/impact/urgency then stable Event ID.
Validate Pydantic JSON/IDs/categories, exactly-one accepted relevant Article assignment,
nonempty Events, cross-run immutable associations and R5 arithmetic/explanations. Count unique Events intersecting the current evaluated relevant Article set,
including referenced historical Events and excluding unrelated historical Events. Enforce in
Python N_events <= N_unique_relevant_articles; report both counts. No output truncation.

## Verification and delivery
Focused cases: related 4->1; two issues 4->2; unrelated 4->4; multi-subject 1->1;
multi-Event group; cross-window/group consolidation; historical identity; duplicate assignments;
all categories; R5/analysis and disabled numerical grounding; explicit failures; atomic restart/cache/locks.
Preserve frozen authentic source text, labels and old results. Report false merges/splits
separately from cardinality; scripted offline replies test contracts, not live semantic quality.
Run uv sync --frozen and complete offline benchmark. No paid calls. Work only in this new
copy; local commit only, no push or PR updates, no merge (latest user instruction).

## Readable summaries and authorized 27-Article live replay
W4 Event summaries use two or three short paragraphs separated by blank lines
when substance warrants it: core issue, important source-supported context, then
conditions or uncertainty. Do not invent headings/facts merely to fill paragraphs.
Streamlit renders each existing paragraph as normal readable body text. For older
single-block summaries, conservatively split only at clear sentence boundaries into
small paragraph groups for display; preserve saved summaries and original Articles.
Numerical prose remains source-grounded model output; Python does not compare
factual numbers with original text. Rubric ranges/weights are not source facts.
The user authorized a real 27-Article replay with the configured model. Reuse all
27 unchanged unique source Articles in the saved full-run result, never its old
Events or scores. Use a fresh v8 database/output, no new search required. Retain
explicit technical failures, bounded automatic item repair and all original outputs. No push.

## Automatic W4 validation recovery
After an Event fails batch JSON/structure/R5 validation, automatically
make at most two single-Event repair calls inside the same run. Stop immediately
on a valid response. Each attempt receives the latest validation error, rejected
response and full unchanged assigned Articles; attempts are numbered in context.
Numerical-grounding recovery is inactive while NUMERIC_GROUNDING_ENABLED=False:
no automatic retries, rejection or forced omission of quantitative prose solely
for a source-number mismatch. Keep substantive Treasury reasoning, source-faithful
attribution, conditional actions and strict integer P/R/U output.
Never rerun W1/W2/W3 or rescore valid sibling Events. No new Agent/job/review stage.
Provider/authentication/credit/transport exceptions stop recovery without repeated
provider requests. Missing original sources and storage failures remain explicit.
If the bounded repairs still fail, preserve the Event unscored with one terminal
error; future explicit runs may resume it. All actual calls/tokens/costs remain in
existing receipts. Public contract8, IDs and SQLite associations stay unchanged; strict structural/R5
validation remains active. Policy fingerprint changes still require a fresh database; never modify
historical markers/fingerprints to bypass compatibility. Offline tests cover
first/second-attempt success, disabled numeric checks, exhaustion, provider stop and valid
sibling/no-rescore isolation. No paid run is required by this implementation task.

## Disabled factual-number checks
The latest user instruction disables numerical source validation by default through
one NUMERIC_GROUNDING_ENABLED=False flag. The existing matching helper remains dormant
for explicit restoration/testing; no active path may call it with the flag disabled.
Keep strict P/R/U integer ranges, weighted total arithmetic, nonempty explanations,
JSON/source membership, cardinality and automatic bounded structural/R5 repairs.
Do not rescore old results, change immutable original text or migrate old databases.
This implementation is verified offline; no paid rerun or push is requested.

## Dashboard execution progress
Persist a separate atomic progress sidecar per database while holding the existing writer lock. Report run ID, UTC start/update timestamps, current stage and stage work counts. This is execution metadata, not a change to Article/Event contracts or classification policy. Readers may inspect progress without starting collection or calling providers. Existing runs without sidecars expose database totals only; never invent percentages.

## ID-constrained parallel W3 execution
Runtime retrieval schemas enumerate only supplied candidate Event IDs. Same/new matching
allows exactly one supplied ID for same and null for new; new IDs remain Python-owned.
Duplicate JSON assignment keys must be detected before dictionary parsing loses them.
Error messages preserve structured missing_ids, duplicate_ids, foreign_ids and invalid references,
plus field locations and allowed IDs as appropriate; never log full Article text or credentials.
Public Article/Event and RunResult contracts remain version 8.

Local consolidation uses bounded deterministic waves: independent decisions see one immutable
canonical snapshot; the main thread reconciles new issues against earlier new issues in the same
wave before committing local merges. Global consolidation still completes before historical identity.
Historical matches run in a bounded pool against the immutable pre-run historical snapshot; the
caller commits decisions in deterministic order, merging into the latest Event state. No worker
uses SQLite. This prevents overwrites when concurrent proposals match the same historical Event.
The existing workers setting (default 4, maximum 8) bounds each W3 pool; pools run in separate phases,
not multiplied. W4 remains default 2 parallel batches of 3 Events. Unique-heavy consolidation can
require additional reconciliation calls and is not guaranteed faster on every input.

Successful immutable Article associations, raw originals, pending work and historical databases
remain untouched. Changed classification policy uses a separate fresh database. The dashboard's
new default is data/news-mvp-v8-id-constrained.sqlite; NEWS_V8_DATABASE can explicitly override it,
and the existing policy guard still refuses incompatible databases. Previously exported news and
saved forecasts remain accessible. No automatic paid re-run or reclassification of old archives.

## Default ten-Article rerun budget

The dashboard collect endpoint and HTTP/CLI run requests default to max_articles=30.
This is a unique selected-Article processing budget across new and resumed work, not
ten Events and not a limit on full source text. Pending proposal units are selected atomically;
work that does not fit remains pending. Unfinished work takes precedence over completed cache hits.
All valid discovery originals may be persisted, but deferred Articles are not classified or
silently added back during consolidation. Historical Event evidence remains complete when a
selected Article matches an existing Event; those prior contextual originals are not new input
Articles. Metrics disclose the limit, selected count and deferred work. A budget is a work bound,
not an error; omitted work must remain available for subsequent explicitly requested runs.
Internal explicit offline replays may use max_articles=None to validate the full frozen corpus.
No new collection is started automatically when changing this configuration.

## Unreferenced local drafts are nonfatal warnings

After exact per-Article assignment and issue-reference validation, discard issue definitions
that have no assigned Article. Record a classify warning with code unused_issue_drafts_removed
and exact removed_issue_ids, retained_issue_ids and article_ids. Keep every supplied Article's
assignment unchanged, including null irrelevant assignments. Duplicate issue definitions,
missing/foreign Article keys, duplicate JSON keys and references to undefined issue handles
remain hard failures, even if unreferenced definitions also exist. This is not arbitrary Event
truncation: only empty local definitions are discarded before persisted Event creation.

Persist warnings in metrics.warnings and the existing per-Article classification metadata;
exclude these known warnings from RunResult.errors and failure status. Cached warnings remain
warnings on restart. A warning-only run completes successfully with no extra model repair call.
The dashboard exposes warnings separately from collection errors. Classification policy changes
use the fresh default data/news-mvp-v8-issue-identity-v2.sqlite; previous databases/exports remain
unchanged, and the default ten-Article budget remains in force.

Unexpected execution failures persist a terminal failed RunResult with the active stage and exception type, without exposing provider response bodies. Original Articles and pending proposals remain resumable. No provider failure is automatically retried.
