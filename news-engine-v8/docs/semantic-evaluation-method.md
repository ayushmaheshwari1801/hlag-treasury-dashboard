# Semantic evaluation method and judge rubric

The benchmark is evaluation-only. It neither alters production JSON nor supplies
reference labels to extraction, association or scoring. Original Board article
body, immutable sources, hashes and historical labels are unchanged.

## Reproduction

Run from news-engine with the frozen environment:

```
uv run --no-sync python -m evaluation.semantic --input outputs/baseline-run --output outputs/semantic-baseline-new --judge
uv run --no-sync python -m evaluation.semantic --input outputs/updated-run --output outputs/semantic-updated-new --judge
```

Use a fresh output directory for every run. Omitting --judge makes no API calls.
Judge calls require explicit authorization. Default judge is the independent-model
`google/gemini-3-flash-preview`; --judge-model overrides it. --judge-repeats is 1 or
2, default 2. Temperature is fixed at zero; no unsupported seed is assumed. There
are no hidden judge repairs/retries. Raw receipts, per-attempt validation failures,
latency, costs and dimension-level repeated-verdict agreement remain in the report.
The CLI returning zero means artifacts were produced, not semantic acceptance.

## Reference provenance and objective metrics

`evaluation/data/semantic_reference.json` freezes source-first Codex annotations,
exact source anchors, rationales, permitted ambiguity and original body hashes.
They are not independent human-adjudicated ground truth. The observed two baseline
proposed false positives were traced separately by an independent code auditor.
Production never receives these labels. A separate official Fed release paragraph
covers adopted requirements with a future effective date without modifying the
historical corpus. The explicit proposal and future-effective reference-only cases
are reported unexercised unless their real sources are actually supplied.

Eleven selected four-source claim anchors are assessed; nine have unambiguous
claim types and two permit defensible alternatives. Normalized contiguous anchor
matching associates references only with descriptions from the same original URL.
It is intentionally conservative: paraphrases or omitted anchors count missing and
need adjudication rather than guessed semantic matching. Missing and conflicting
predictions stay in the confusion matrix and denominator. Per-class precision,
recall and F1 include missing reference claims as false negatives; zero-support
undefined metrics are null. Proposed false positives count non-proposed reference
claims predicted proposed. Both missing-inclusive and matched-only accuracy are
reported. This selected-anchor coverage is not exhaustive extraction recall.

Cohort files are processed in filename chronology, as emitted by the authentic
runner. Each Event's latest snapshot replaces its older canonical Change set;
a formerly canonical Change removed later cannot remain a phantom success. Immutable
Article conflicts fail explicitly. Evidence checks validate exact original body and
quote offsets. Zero invalid spans does not prove factual interpretation or atomicity.

The report copies original workflow grouping/retrieval/R5/cost metrics and reads
SQLite unmatched counts in read-only mode where available. Zero unmatched work does
not establish correct association. Category checks accept documented facility
regulatory/macroeconomic alternatives; this regulatory-heavy sample does not measure
Industry or macro-only recall. Scoring and summary truth remain unestablished by
objective tests, rather than being inferred from valid arithmetic/JSON.

## Judge rubric and evidence contract

The structured judge receives all four original workflow Article bodies even when
production failed to return one, observed/expected source IDs, Article-ID links,
latest Events with accepted Changes/status/category/score, and each cohort's final
summary/errors. Reference labels are excluded from judge context.

For each of seven dimensions it emits supported/unsupported/uncertain, an explanation
and at most one short original-source citation:

- Extraction: factual accuracy, atomicity, material qualifications and missing claims.
- Association: coherent issues, justified merge/split; narrower SBA/Board boundaries
  can be defensible, shared program keywords alone cannot justify a merge.
- Regulatory status: final/adopted versus proposed, separate adoption/implementation,
  future effective dates, open comments and mixed actor/status claims.
- Classification: underlying development, not possible Treasury impact channel.
- Provenance: the correct Article and exact supporting original passage.
- Scoring: semantic P/R/U bases, company applicability, evidence gaps and cross-score
  consistency; arithmetic alone is insufficient.
- Summary: source/actor/status/relative-time fidelity, material conditions and invented
  conclusions, including unsupported claims of absent company exposure.

Each citation must identify a supplied source and an exact substring. Invalid
citations invalidate that judgment explicitly. Ambiguous/disputed cases should be
uncertain and request human adjudication. No detected error is not proof of full
coverage. Judge outcomes never rewrite production results. Two temperature-zero
judgments from the same independent model are correlated advisory evidence; agreement
is not independent ground truth or a confidence guarantee. Where judge/model settings
or structured output fail, costs and failures are still reported without substitution.

Supply your saved runs; earlier local outputs were removed on request. Active
status metrics use four Claim Types. Obsolete labels in supplied historical output
are counted in an unsupported prediction column, with raw predictions retained.
