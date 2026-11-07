# Evaluation

The active contract 8 acceptance gate is `python benchmark.py --profile offline`.
It uses deterministic scripted model replies, including unchanged authentic news
sources, to verify classification/association/cardinality/analysis contracts. It does
not measure a live model's semantic quality.

`python -m evaluation.mvp --selection smoke` is an optional paid chronological replay;
`--selection full` uses all 72 authentic sources. Never invoke either without explicit
authorization. `--validate-only` checks frozen corpus integrity without provider calls.
The replay reports current evaluated Articles/Events, cross-run associations, missing
sources and pairwise false merges/splits against existing historical labels.

Corpora, frozen labels and historical reports remain unchanged. Historical labels
were authored for earlier issue boundaries; pair scores are diagnostics rather than
proof of dominant-issue classification quality. Gold labels never enter model inputs.

`semantic.py`, Event/MajorChange acceptance helpers, dataset builders, observation
metrics and directories named legacy_contract* are historical research/evaluation
adapters for saved older contracts. They are not production runtime paths or default
contract 8 gates. In particular historical Claim Type/extraction/progression metrics
do not apply to contract 8. Preserve historical reproducibility; do not reinterpret
old outputs as schema 8 or silently overwrite old results.
