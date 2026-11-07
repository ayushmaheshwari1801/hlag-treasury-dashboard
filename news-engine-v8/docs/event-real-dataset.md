# Workflow E real-source dataset v1

`tests/fixtures/event_sequences_real_v1.json` contains **72 distinct authentic
public releases and 23 business occurrences**, replacing no previous dataset.
The source texts were downloaded on October 30, 2026; their publication dates span
2020–2024. These are selected original paragraphs, not generated or paraphrased news.

| Stream | Unique releases | Occurrences | Sources |
| --- | ---: | ---: | --- |
| liquidity-us | 36 | 12 | Federal Reserve (31), U.S. Treasury (5) |
| mergers-global | 36 | 11 | FTC (17), UK Government/CMA (19) |

The historical E-only replay starts each stream with an empty database and interleaves different occurrences in
publication order. Microsoft/Activision and NVIDIA/Arm evidence from both jurisdictions
shares one stream. Different facilities share the Federal Reserve as issuer, testing
whether similar keywords incorrectly merge different business scopes. Each source
appears once; repeats and excerpts do not inflate the unique-article count.

## Annotation and evidence

The **OpenAI Codex assistant** selected and read the passages and assigned labels,
as requested by the user. No model predictions were inspected before these labels
were frozen. There is **one AI annotator and no independent human adjudication**.
The initial run used this new corpus without prompt tuning. Subsequent scoped-completion
and identity-policy fixes use observed errors to improve the workflow, so subsequent
scores are development-benchmark results, not unseen held-out performance. Labels and
source text remain frozen. Historical public releases may be in model
pretraining, so this is not a guaranteed unseen-data or contamination-free benchmark.

Each row stores its original URL and title, selected verbatim paragraphs, publication
date, SHA-256, event membership, lifecycle, decision, development kind, scope,
annotation reason, and identity/state confidence. `event_real_sources_v1.json` stores
the extracted source paragraphs, raw-HTML hash, text hash and retrieval time. Paragraph
indices are zero-based into that archive. Whitespace is normalized; facts are not
rewritten. Noncontiguous selected paragraphs are joined with blank lines and their
indices remain explicit. No annotation reason or event catalog is sent to the model.

The sources are US federal-government public information and UK government releases
attributed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Use original URLs and archived paragraphs to audit the labels. Source snapshots are
current downloaded pages, not verified contemporaneous web archives; later publisher
edits cannot be ruled out.

Publication precision is deliberately **date-only**. Midnight UTC encodes that date;
it does not claim the release appeared at midnight. Replay discovery is simulated
at 23:00 UTC with ordinal seconds. Same-day ordering is deterministic: Fed before
Treasury, then URL. That tie-break is an evaluation convention, not factual intraday
chronology. Occurrence dates and update dates must still come from source evidence.

## Label policy and difficult boundaries

Event means one business occurrence with a defined actor and scope. Development
means progress within it. Article/Mention supplies evidence. A regulator or source
publisher is not automatically the event subject.

- Facility eligibility, pricing and expiry changes stay in the same program. PMCCF,
  SMCCF, PPPLF, MLF, BTFP and other facilities are distinct occurrences. The PPP
  bank-insider exception and bank-capital rule are also separate from PPPLF funding.
- Acquisition review, injunctions, appeals, remedies and regulatory disposition are
  progress of the same business transaction. A final competition clearance does not
  establish actual transaction closing. A preliminary injunction does not establish
  abandonment. Active transaction proceedings are labeled ongoing.
- The narrower Microsoft/Activision replacement structure retains acquisition identity
  under this business-level policy. This is the one medium-confidence identity label;
  a docket-level policy would differ and needs explicit versioned adjudication.
- Planned closure remains ongoing. Explicit actual abandonment or facility closure
  can resolve the occurrence. Main Street remains announced until implementation is
  evidenced; lender registration marks implementation, before full loan-purchase readiness.
- Revised provisional findings and appellate reversals are new procedural developments,
  not simultaneous factual conflict or errata. Official endorsements of already reported
  terms can corroborate; they are not syndicated duplicates.
- Confirmation means the source reports an actual official action. Alleged competitive
  harm and future consequences retain their qualifications; they are not asserted facts.

There are **56 high-confidence and 16 medium-confidence state labels**, and 71 high-
confidence / one medium-confidence identity label. The full score uses all 72 fixed
labels; the high-confidence state subset is reported separately. These flags identify
annotation uncertainty, not permission to count a mismatch as correct. Any later
adjudication must create v2 and score the same predictions against both label versions.

| Label | Count |
| --- | ---: |
| create / attach / corroborate | 23 / 45 / 4 |
| announced / ongoing / resolved | 14 / 55 / 3 |
| new_development / corroboration / resolution | 66 / 4 / 2 |

No rumors, simultaneous factual conflicts, corrections, abstention negatives or
syndicated duplicates are fabricated to fill categories. Those remain covered by
synthetic contract tests. Ten collected sources are excluded with recorded reasons:
multi-facility updates, cross-facility regulation, trial-access logistics or unrelated
standalone enforcement. The sample is purposive, official-English, and limited to
one primary occurrence per selected passage. It does not measure unrestricted
multi-event full-article extraction or population-wide news accuracy.

## Current V2 evaluation

The current full-service benchmark reuses all 72 frozen source passages in 57
chronological cohorts and executes MajorChange extraction, Event resolution,
scoring and reports. It does not feed historical frozen W2 output to V2.
The source-only replay excludes live search, fetching and HTTP transport latency.
See [the evaluation guide](evaluation.md) for current quality denominators,
checkpoints and the separate extraction benchmark. Event labels alone do not
establish MajorChange recall or numerical and directional accuracy.

```sh
uv run --no-sync python -m evaluation.validate_real_events --output outputs/real-annotations
uv run --no-sync python scripts/check_real_corpus.py
uv run --no-sync python -m evaluation.service_benchmark --selection full \
  --model google/gemini-3-flash-preview --no-reasoning --output outputs/service-new
```

Use a fresh output directory for a changed execution policy. Keep original source
archives, labels and failed runs intact.

## Historical E-only evaluation

The following records the original evaluation design and results; it is not an
active execution path. W2 was frozen as the original selected evidence with
confirmed source-action status; W1/W2 quality was not measured. The evaluation
ran production E1/retrieval/E3/review/persistence with its own accumulated state. Never change labels or discard
failed rows to improve a score. Freeze program/prompt/source-policy versions per run;
document any later product fixes and retain the original reports. The initial runs'
source policy omitted FTC/CMA/Treasury announcements. The scoped-completion fix adds
exact announcement paths and issuer/provenance checks, separately regression-tested;
it also reviews occurrence-level completion and filters invalid optional Watch links.
The same 72-source replay after these changes measures development progress only.

The initial `5120fd7` run used the legacy 1,600-byte candidate budget and recorded
54 budget rejections. The requested historical follow-up disabled this budget with
`max_tokens_per_candidate: null`, keeping the exact source/label files and prompts.
It retained complete first/latest evidence, allowing additional anchor evidence
to reach E3. Preserve both reports; configuration and model variability mean the
comparison is diagnostic rather than a controlled estimate of model accuracy.

Reports include association, decision, state, high-confidence state, development,
false merges/splits, conditional retrieval recall, article-pair membership precision/
recall/F1, stage failures, replay calls, token receipts and cost. Pairwise scoring only
compares articles in the same database; missing links count as false negatives.
Association measures ID membership, not independent semantic correctness of every
extracted actor/action/time. Failures remain in all end-to-end denominators.

The historical `Workflow E checks` ran each 36-source stream in a separate matrix
job and combined only matching, complete, non-overlapping reports. Its combined
`event-real-report` artifact contained JSON, standalone HTML, PNG/SVG and the
annotation audit HTML. Incomplete stream artifacts were retained separately.
Those original artifacts and measurements remain historical evidence; use the
current V2 commands above for new execution.

The planned 150–300-source, independently adjudicated benchmark remains future work.
