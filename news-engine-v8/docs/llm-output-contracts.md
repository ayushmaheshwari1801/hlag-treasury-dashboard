# Article classification LLM contracts ? contract 8

| Stage | Model contract | Python checks | Calls |
| --- | --- | --- | --- |
| W1 Search | QueryPlan with topic/geography/time-aware queries | Topic enum, bounded query budget, source records | One plan plus bounded Exa searches |
| W2 Grouping | Deterministic lexical/time groups | Complete exactly-once Article coverage, seven-day window, max four Articles | Zero |
| W3 Classification | Full grouped Articles -> complete, events[{title,summary,category,article_ids}], irrelevant_article_ids | Every supplied Article exactly once in one Event or irrelevant list; no foreign IDs; nonempty Events; category enum | One per Group |
| W3 Consolidation | Local proposals vs temporary D-handles, then historical/current E IDs -> same/new | One supplied candidate for same, none for new; cross-group merge before persistent allocation; atomic assignment/consumption | Existing bounded identity budget, zero with no candidates |
| W4 Analysis | Final Event and all original Articles/publication dates, shared Treasury context/rubric -> results[{event_id,analysis}] | Exact Event ID, independent structures, strict R5/arithmetic/explanations, numerical source checks disabled | One per batch and at most two automatic repairs per failed Event |
| W5 JSON | Ranked Events and original sources | schema_version 8, assignments/cardinality, deterministic overall summary, explicit errors/metrics | Zero |

Articles are untrusted data, never model instructions. One Article may discuss several
issues but receives only its dominant Event label. Local Groups are provisional: W3
can merge related issues across Groups and time windows. Shared country/industry/program
alone never establishes identity. Matching is forced same/new, merge-first for reasonably
related issues, without confidence gates or review stages. Python owns stable Event IDs.

No MajorChange, EventEntry, NumericObservation, Claim Type, What Changed, entry evidence,
novelty relation or progression contract remains. Event source information is the full
associated Article. Model summaries preserve attribution and uncertainty; financial
implications/actions remain conditional when company exposure is unknown.

W4 returns an informative summary, why_it_matters, what_to_do, P/R/U and nonempty
reasoning. R5 anchors remain advisory while R5_CAPS_ENABLED=False; dimensions are strict
integers 0..5 and total is exactly 8P+7R+5U. Prompts require source-faithful numbers, but Python source-number matching is disabled
by NUMERIC_GROUNDING_ENABLED=False. No numeric extraction or validation Agent exists.

Batch size defaults to three Events (max four), analysis workers to two (max eight).
Result order is arbitrary; bind by Event ID. Missing/duplicate/invalid items trigger only
the affected Event's bounded automatic repairs. Unknown returned IDs are explicit errors. Keep valid
siblings; worker threads never access SQLite. Unchanged inputs reuse scores.

Every provider request uses the strict Pydantic response schema. Full original Articles
are passed without application truncation/length rejection. Requests omit max_tokens,
max_completion_tokens and max_output_tokens. Provider capacity and incomplete responses
remain explicit failures; call/query/concurrency budgets and token/cost receipts remain.

Automatic W4 recovery stops after success or two single-Event repair calls. With numerical source checks disabled,
number mismatches do not cause retries or force qualitative narrative. Successful siblings and
existing scores are reused; Search and classification are not repeated. Provider,
authentication, credit and transport failures stop immediately. Exhausted Events
remain explicitly unscored/partial. Actual calls, tokens and costs are recorded.
