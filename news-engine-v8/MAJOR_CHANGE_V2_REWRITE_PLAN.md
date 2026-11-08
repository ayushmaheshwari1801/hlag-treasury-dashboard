# Major Change Intelligence V2 — Core Rewrite Plan

**Target repository:** `yuguocc/agentic-treasury-dashboard`  
**Baseline:** `main` at `c0fe0b12257349bf5051bd1159ab63d29ce4ea69` (recheck the branch head before making changes)  
**Scope:** Rewrite the *news intelligence core*, reuse existing infrastructure. This is a build specification for Codex, not a claim that the proposed code is already implemented.

## 0. Executive decision

The accepted extraction/review correction in Plan v7 section 17.2 supersedes
earlier treatment of numerical bounds as qualitative-only: explicit levels and
qualified thresholds retain their numbers without invented comparisons. Extraction
does not apply R5 relevance or require globally novel Events. See
[the corrected contract and exact five-source audit](docs/v2/extraction-contract-review.md).

Replace the fact-centric processing path:

`Article -> all Facts -> selected Major Facts -> Event Candidates -> Event -> Event Score -> Brief`

with:

`Article -> Major Changes (numeric / directional / status) -> Event resolution -> Event-version priority -> Brief / Dashboard`

A **MajorChange** is the single canonical extracted unit. When a reported change includes a verifiable number, keep the number, its metric and units; otherwise preserve the supported qualitative trend or status transition. Do **not** extract generic background facts for their own sake.

**Do not rewrite:** Exa search transport, LLM provider client, original-article ingestion, SQLite transaction primitives, authentication/job HTTP infrastructure, Streamlit framework, historical benchmark corpus, or the R5 Treasury scoring rubric. Reuse their stable interfaces wherever possible.

**Do not delete or rewrite historical data.** Build and test V2 on a fresh, isolated SQLite database and output directory. Retain V1 in Git for comparison. Avoid permanently operating two competing pipelines after cutover.

## 1. Product behavior and non-goals

### Required behavior

- Detect significant, source-supported **new decisions, measured changes, shifts in qualitative conditions, announcements, implementations, reversals, resolutions and deadlines** in the configured Regulatory, Macroeconomic and Industry scope.
- **Numeric:** extract observed level and/or reported delta, previous level, metric, period, direction and exact unit **only when supported**. A delta alone is valid; it does not imply the current level is known. Keep unsupported fields `null`.
- **Directional:** when the source gives a trend without a defensible amount, preserve `increase`, `decrease`, `tightening`, `easing` or another explicit direction, with `numeric: null`.
- **Status:** when there is no numeric trend but a concrete state transition, preserve `announced`, `implemented`, `suspended`, `resumed`, `resolved`, etc. An unspecified prior state is `null`.
- Preserve qualifiers: observed vs announced vs proposed vs forecast vs unverified/disputed. A confirmed source report of a proposal is **not** proof that the proposal is effective.
- Each change must carry exact, immutable article-revision evidence ranges. Disjoint excerpts remain separate; never expand a citation across unreferenced text.
- The same occurrence across several articles becomes one Event with separately recorded developments/corroboration. An article can contain multiple independent changes/events.
- A source with **no relevant major changes** may return `[]` **only if** its source and change coverage are complete; never convert extraction failure, truncation, or ambiguous omissions into an empty success.
- Reuse previously accepted unchanged content on replay; do not repay model calls unnecessarily.

### Non-goals in this rewrite

- Internal Treasury exposure, cash forecasting, sensitivities, market-price feeds, proactive notifications, and actual policy optimization. Those are downstream products, not prerequisites of this rewrite.
- A new Agent framework, vector database, message queue or microservice architecture.
- Automatically reprocessing or mutating V1 immutable accepted records.
- Inferring actual company losses, holdings, hedge positions or financial impacts from news alone.

## 2. Canonical contracts

Use Pydantic strict schemas and explicit `schema_version` in every persisted/exported V2 contract. Naming below is conceptual; keep cross-file field definitions consistent.

```python
class EvidenceRef:
    article_id: str
    revision: int
    source_range: {"start": int, "end": int}  # one-based inclusive

class NumericChange:
    metric: str
    current_value: DecimalString | None
    previous_value: DecimalString | None
    delta: DecimalString | None
    value_unit: str | None
    delta_unit: str | None
    current_period: str | None
    previous_period: str | None

class EventHint:
    subject: str
    action: str                  # stable occurrence type, not latest phase
    scope: str                   # one identifiable business occurrence
    location: str | None
    identifier: str | None
    event_at: datetime | None    # explicit original occurrence anchor only

class MajorChangeDraft:
    local_key: str
    description: str
    change_type: Literal['numeric', 'directional', 'status']
    direction: str | None
    numeric: NumericChange | None
    status_from: str | None
    status_to: str | None
    claim_type: Literal['observed', 'announced', 'proposed', 'forecast', 'reported_unverified']
    verification: Literal['confirmed', 'unverified', 'disputed']
    effective_at: datetime | None
    development_at: datetime | None
    evidence: list[EvidenceRef]       # nonempty
    event_hint: EventHint             # present for every trackable occurrence

class MajorChange(MajorChangeDraft):
    change_id: str                   # generated by code, stable within frozen policy
    article_id: str
    revision: int
    extraction_policy: str

class MajorChangeExtraction:
    schema_version: Literal[1]
    article_id: str
    revision: int
    category: Literal['Regulatory', 'Macroeconomic', 'Industry']
    changes: list[MajorChange]
    source_coverage_complete: bool
    change_coverage_complete: bool
    unprocessed_ranges: list[SourceRange]
    diagnostics: list[str]
```

**Numeric semantics:**

1. Decimal strings, never JSON floats. No unsupported magnitudes.
2. Preserve level vs delta, percent vs percentage points vs basis points, dates and periods.
3. Recompute delta in Python when comparable levels and units are given. If the source **only reports a delta**, retain that sourced delta and leave levels null; validate its sign and unit against evidence.
4. A numeric observation is not necessarily a *change*: e.g. an unchanged benchmark or static background amount must not become a MajorChange without a supported material development.
5. If numeric evidence is ambiguous or qualified as a bound/forecast, do not reclassify it as an exact observed figure; retain the qualitative change/qualification instead.

**Identity/time semantics:** `published_at`, `known_at`, `event_at`, `development_at`, `effective_at`, `recorded_at` and `score_available_at` are distinct. Unknown occurrence timestamps stay unknown; publication dates cannot be substituted.

**Coverage semantics:** Keep both source-range completeness and material-change completeness. No arbitrary global `max 2 changes` cap. Split oversized inputs, preserve completed chunks, and surface outstanding ranges.

**Model roles:** LLM proposes change descriptions, event identity hints and optional numeric fields. Python owns stable IDs, evidence extraction, arithmetic checks, range/foreign-key constraints, time eligibility, persistence and scoring total calculation. Never accept model-supplied permanent Event IDs.

## 3. Revised execution flow

```text
W1 SearchPlan + Exa (reuse)
    -> Article ingestion / URL revisions / duplicate groups (reuse)
    -> V2 Change Extraction (LLM once per normal source chunk)
         -> source/numeric validation (Python)
         -> bounded semantic coverage review (LLM; targeted repair on failure)
         -> durable accepted Change checkpoints
    -> V2 Event Resolution
         -> keyword/time candidate retrieval (reuse policy)
         -> deterministic exact match when provably safe; otherwise LLM association
         -> preserve create / attach / corroborate / conflict / defer
         -> new material development => Event version update
    -> V2 Event Scoring
         -> existing R5 P/R/U dimensions and caps
         -> evidence/version-bound score and review
    -> V2 Weekly Brief
         -> rank eligible Event versions by score
         -> hydrate only verified Change evidence and source links
         -> Treasury transmission pathways / suggested checks / executive summary
    -> SQLite / JSON exports -> Streamlit + HTTP results
```

**Do not merge separate policy announcements, facilities or independent occurrences just because a subject matches.** Do not turn a second source confirming the same statement into another material Event version. Retractions/corrections remain auditable and must not silently leave withdrawn evidence active.

## 4. File-level rewrite map

| Existing file(s) | Action | Target ownership |
|---|---|---|
| `collect.py`, `search_history.py`, `sources.py` | Reuse; minimal glue | Search/ingestion and dedup |
| `clients.py`, `semantic_review.py` | Reuse; instrument latency | LLM provider and bounded review calls |
| `models.py`, `extract.py`, `prompts/extract.txt`, `prompts/extraction_review.txt` | Implement separate V2 counterparts first | `MajorChange` contract + extraction/review |
| `major_facts.py` | Retire from V2 | No posthoc top-2 fact selection |
| `extraction_chunks.py`, `extraction_store.py` | Adapt/wrap for V2 | Chunk identity, partial coverage, safe replay |
| `pipeline.py`, `article_projection.py` | Replace V2 source execution; keep Watch only if configured | Source orchestration + optional Watch metadata |
| `event_facts.py`, `event_models.py`, `events.py`, `event_store.py`, `event_retrieval.py`, `event_inference.py`, `event_batches.py` | Adapt matching/ledger; replace fact references | `change_refs` and source-grounded Event developments |
| `event_scoring.py`, `event_score_store.py` | Preserve R5/deadlines/review; hydrate Changes | Event-version priorities |
| `w4_facts.py`, `w4_event_selection.py`, `w4_brief.py`, `w4_editorial.py`, `w4_semantics.py` | Replace Fact Catalog with Change Catalog; otherwise reuse report rules | Verified Event-first report |
| `exports.py`, `streamlit_app.py` | Version outputs; update reader | Article sources, Major Changes, ranked Events, reports |
| `orchestrator.py`, `execution.py`, `api.py`, `workflow_jobs.py` | Reuse parent job, stage checkpoint, authentication and resume; minimal integration | Full workflow/HTTP |
| `evaluation/*`, `scripts/check_*.py` | Reuse framework and real corpus; adapt gold/metrics | Quality, recovery, latency, cost acceptance |

Implement new core in a **temporary `news_agent/v2/` namespace** (or equivalent isolated branch-local modules), not by replacing old models in-place before consumers compile. Do not duplicate or rewrite stable infrastructure under `v2/`. Remove the transitional version switch after the V2 cutover; keep V1 source available in Git.

## 5. Storage and external outputs

Keep original `articles` and the existing infrastructure's DB transaction/job primitives. On a **new V2 database** use a small set of logically canonical records:

- `major_change_extractions`: immutable extraction policy, source coverage, chunk status and accepted result.
- `major_changes`: `change_id`, source-revision identity, description/type/numeric/status/qualification and evidence JSON (or normalized evidence rows if needed).
- `events`: stable event identity, lifecycle, verification, current version and material anchors.
- `event_developments`: event version, change references, source, kind, recorded/known times and correction history.
- `event_scores`: version-bound R5 assessment + audit + scoring time/policy.
- `briefs`, `runs`: reuse existing logical ownership and versioning where structurally safe.

Prefer one immutable evidence reference contract (`ChangeRef`) over maintaining both a FactRef and an additional ChangeRef. A ChangeRef contains `change_id`, `article_id`, `revision`, `extraction_policy` and exact cited ranges. All Event scoring and report evidence resolve through it. Reject references to foreign, rejected or unavailable source versions.

**Draft exports, separate from existing outputs:**

```text
news-engine/outputs/v2/
    major_changes_latest.json
    events_latest.json
    treasury_brief_<YYYY>W<WW>_v<N>.json
    monitoring_<run_id>.json
```

At cutover, bump the public JSON schema versions, make the UI and clients explicitly consume those schemas, and maintain the existing `/api/v1/jobs` job-control contract where possible. For incompatible *result payloads*, introduce versioned result shapes or a new route; never serve silently changed JSON under an unchanged contract. Document whether the downstream caller uses `schema_version` or URL version for negotiation.

## 6. Implementation phases (Codex task order)

### Phase 0 — Freeze baselines and dependencies

1. Record current SHA/branch; create `refactor/major-change-v2`.
2. Inventory all live references to `fact_ids`, `fact_refs`, `major_fact_ids`, `canonical_context`, `ArticleAnalysis`, Event scoring and exports. List active vs tests-only dependencies.
3. Freeze the current 72-source real corpus, 4-source smoke, current prompts/models and costs. Baseline current offline suite. If live credentials exist, collect a V1 smoke with calls, tokens, latency, coverage and errors. Otherwise state that live comparison was not run.
4. Write an explicit V1-to-V2 contract map and a short ADR describing why this is a breaking rewrite with a separate DB.

**Exit:** dependency map, recorded baseline and passing unchanged offline baseline (or documented existing failures).

### Phase 1 — Models, extraction and numeric evidence

1. Add V2 strict Pydantic contracts, versioned result and `change_id` assignment.
2. Implement change-focused prompt: all material occurrences, numeric if supported, qualitative otherwise; no generic fact list.
3. Reuse exact source segmentation and Decimal validation. Implement `numeric` level/delta/units checks and disjoint evidence integrity.
4. Keep one bounded semantic coverage review of extracted changes. Repair only rejected or missing coverage, without discarding accepted siblings. Distinguish invalid/no-change/incomplete.
5. Keep durable per-chunk retry and source revision/policy fingerprinting, not full reruns after partial failures.

**Exit:** end-to-end `Article -> accepted MajorChangeExtraction` with frozen recorded-provider tests.

### Phase 2 — Event resolver and ledger

1. Adapt candidate construction from `MajorChange.event_hint`, not `FactDraft` or independent Event Candidates.
2. Keep existing retrieval (keywords/time) and conservative `create/attach/corroborate/conflict/defer` contracts.
3. Store material `change_refs` on Event developments; do not materialize Fact IDs.
4. Preserve event identity across phase changes; corrections, verification, lifecycle and historical as-of projections; reject invalid material refs before commit.
5. Reuse Event score queue only after a valid, material new version is committed; corroboration should not trigger unnecessary rescore.

**Exit:** same-occurrence and separate-occurrence multi-article scenarios pass, with zero dangling evidence and correct replay.

### Phase 3 — Event Scoring, reports and UI

1. Keep R5's `P`, `R`, `U`, formula `8P + 7R + 5U`, caps and Treasury context; switch score evidence inputs to accepted Changes.
2. Keep version-bound score state, deadline re-evaluation where supported, and known/available-at cutoffs. Never claim company-specific exposure estimates.
3. Replace W4 Fact Catalog with a bounded Change Catalog. The report can quote verified Changes but cannot invent new observations.
4. Export articles/source metadata, ranked Events and relevant Changes; update Streamlit for Numeric/Directional/Status display, evidence links, scores and coverage gaps.
5. Integrate into one logical run and existing API jobs, using fresh V2 output contract versions.

**Exit:** full initial and incremental V2 workflow produces nonempty source-grounded Event-first reports where expected; frontend and saved report agree.

### Phase 4 — Evaluate, optimize and cut over

1. Run existing `benchmark.py --profile offline` on the relevant unmodified V1 baseline and equivalent V2 checks.
2. Run 4-source real-model smoke and the 72-source historical service benchmark against V2, reporting failures rather than omitting them.
3. Existing 72 Event labels can assess identity/lifecycle, **not** MajorChange precision/recall. Create a *separately labeled*, preferably independently human-adjudicated MajorChange gold subset for numeric, directional, status, proposal, no-change, duplicate and correction cases.
4. Compare V1 vs V2 on the **same model, corpus, reporting clocks, model settings and measurement method**: Event recall and false merges/splits, supported change precision/recall, numeric/unit accuracy, qualitative direction, evidence grounding, report support, calls/tokens/cost, p50/p95 article latency, throughput and recovery.
5. Inspect every critical missing occurrence, invented number, false resolution and unsupported report claim. Do not award throughput wins to runs that omitted required Events.
6. Once data and API contracts/tests are satisfied, switch CLI/API/Streamlit to V2, remove obsolete runtime code/prompt calls (not historical datasets), update docs, and retain a rollback commit/tag plus DB backup.

**Exit:** full operational regression passes, explicit live limitations/quality results published, and a rollback procedure exists. If quality is below baseline, do not cut over even if calls are lower.

## 7. Acceptance tests and invariants

**Contract tests (must pass):**

- Numeric with levels and correct delta; numeric with **delta only**; numeric level without a supported change is not promoted automatically; percent versus percentage points; unit and sign preservation.
- Qualitative tightening/easing, strengthening/weakening with explicit base currency, and no invented magnitude.
- Proposal/forecast versus implemented/observed; authoritative resolution versus expected reopening; a withdrawal/correction invalidates active evidence without rewriting history.
- Multiple independent Events in one article; multiple Changes about the same Event; repeated syndicated article; duplicate corroboration does not create another material version.
- Disjoint citations, article revisions, source cutoffs, unknown dates, changed source body and missing references.
- Fully processed zero-change input versus incomplete coverage, invalid structured output, provider refusal, truncation, transport failure and credit exhaustion.
- Long sources that must chunk, checkpoint, resume and preserve accepted earlier chunks; partial recovery does not double-count new Events or repeat paid calls unnecessarily.
- Scoring P/R/U arithmetic/caps, event-version binding, old-versus-new score availability and no fabricated company exposure.
- W4 source references and report changes exactly match accepted Change IDs; UI displays `numeric: null` honestly and never treats a qualitative trend as a numeric measurement.
- HTTP authentication, idempotency, job resume, output schema contract, saved data restart and status propagation.

**Quality metrics:** material-change recall/precision, numeric accuracy, qualitative direction accuracy, reference support rate, event association quality, false resolution count, report faithfulness, and explicit incomplete/failed counts. Gold must be independent of the evaluated model where feasible. Record denominators and keep failures in them.

**Performance metrics:** model calls per successfully processed article, extraction prompt/completion tokens, elapsed time, p50/p95 latency, throughput, provider-reported cost, semantic review/repair rate and replay reuse. A lower call count is not an acceptance pass by itself. Parallelize independent articles only after the serial V2 contract is stable and Watch/follow-up dependencies are frozen.

**Benchmark commands (existing framework):**

```bash
cd news-engine
uv sync --frozen --extra evaluation
uv run --no-sync python benchmark.py --profile offline
uv run --no-sync python -m evaluation.service_benchmark --selection full --validate-only
# With configured, explicitly authorized paid test credentials:
uv run --no-sync python -m evaluation.service_benchmark --selection smoke \
  --output outputs/v2-service-smoke --model deepseek/deepseek-v4-flash --no-reasoning
uv run --no-sync python -m evaluation.service_benchmark --selection full \
  --output outputs/v2-service-full --model deepseek/deepseek-v4-flash --no-reasoning
```

Codex must update the runners as necessary to select V2 without feeding gold answers into inference. Do not run the full paid corpus by default if credentials/budget are unavailable; report the blocked check.

## 8. Implementation constraints

- Avoid new frameworks/services. Favor a small, synchronous pipeline first; concurrency is a separate measured improvement.
- Do not confuse *few lines/files* with lower operational complexity. Preserve valuable source and lifecycle safeguards.
- All business-layer outputs must cite accepted sources. The model cannot create permanent IDs or determine verified completion alone.
- Do not delete accepted V1 data or migrate it by rewriting evidence. Deploy V2 on a new database by default; historical migration is separate and explicitly authorized.
- Distinguish a business-rule change from a source extraction-policy change in fingerprints and recovery.
- Maintain clear logs of missing coverage, cost, validation errors and quality regressions.
- Avoid an indefinite compatibility layer. Temporary adapters only during cutover, with clear removal criteria.

## 9. Deliverables to provide in the PR

1. Short architecture diagram and V1→V2 dependency/migration map.
2. V2 code, prompts and DB/output schemas with clear ownership.
3. Focused tests + offline regression results, with real-source smoke/full reports if run.
4. Side-by-side cost, speed and accuracy comparison including failed articles and quality limits.
5. Documented cutover, database isolation, rollback procedure, and file deletion list.

## 10. Instructions to Codex

Work in **small, reviewable commits** by Phase. Inspect the current branch head and existing implementations before editing. Implement rather than stopping after producing another plan. Start with Phase 0 and Phase 1, run focused and offline checks, then proceed to downstream stages when contracts are stable. Do not claim acceptance before quality and performance evidence is available. Never delete historical datasets or user databases.
