# Portable real-news workflow corpus

This directory contains **72 unique authentic official news releases and 23 annotated
business occurrences**. It reorganizes the existing frozen Event corpus into TXT and
CSV without changing source text or Event labels.

| File | Purpose |
| --- | --- |
| `sources.csv` | Source IDs, titles, URLs, publication/discovery times, provenance, licensing and text hashes. |
| `sources/<source_id>.txt` | Exact UTF-8 selected source passages used as workflow input. No added header or generated news. |
| `archive/<source_id>.txt` | All paragraphs saved in the original source archive, for provenance review. These are not automatically the model input. |
| `gold_events.csv` | Existing frozen Event membership, state and development labels, with annotation confidence and rationale. |
| `gold_workflow_smoke.csv` | Four source-specific W2/W3/W4 qualitative expectations; single-assistant review guidance, not a complete semantic scorer. |
| `scenarios.csv` | Four-source/two-week smoke and the 72-source/57-week chronological timeline. |
| `cases.csv` | Real-service continuity checks and links to existing deterministic recovery/security/negative tests. |
| `manifest.json` | File hashes, original dataset hashes, annotation policy and limitations. |

The historical sources were retrieved on October 30, 2026. Publication dates remain
2020–2024. Midnight UTC publication timestamps encode source dates, not known event
times. Discovery timestamps are simulated chronological replay clocks. Each full
scenario cohort uses its original UTC reporting week and the following Monday's
publication clock. The service runner first ingests the sources before that week's
cutoff, then requests the weekly report on Monday; both HTTP jobs and their calls
are retained. Dates are never compressed into artificial adjacent weeks.

The smoke contains PPPLF announcement and capital-rule releases on April 6 and 9,
2020, followed by the operational PPPLF update and separate insider-lending rule on
April 16 and 17. It exercises both existing-event continuity and distinct business
scopes across consecutive weeks. The full timeline mixes the original domain streams;
this adds cross-domain retrieval competition compared with the historical E benchmark.

Source loaders expose article data only. They do not load `gold_events.csv`, workflow
guidance, or the original fixture's frozen W2 analysis into model contexts. Full
service runs generate fresh W2, W3, E and W4 outputs. HTTP replays and resumed jobs
reuse the same sources; they are not counted as additional authentic news.

These are selected official English-language passages, not a representative sample
of unrestricted full articles. State labels are from one AI annotator and include
recorded uncertainty. Rumors, conflicting reports, corrections and syndicated
negative cases remain in the existing synthetic contract suites. The corpus does
not claim independently adjudicated W1/W2/W3/W4 semantic accuracy.

From `news-engine/`:

```sh
uv run python -m evaluation.real_corpus
uv run python -m evaluation.service_benchmark --selection smoke --validate-only
uv run python -m evaluation.service_benchmark --selection smoke \
  --output outputs/service-smoke --model deepseek/deepseek-v4-flash --no-reasoning
```

See [the service benchmark guide](../../../docs/service-benchmark.md) for full runs,
output interpretation and coverage. Rebuilding with
`uv run python -m evaluation.real_corpus --rebuild` reproduces the TXT/CSV files from
the existing frozen JSON archive. It makes no network or model calls.

Original-source licensing information is preserved per source in `sources.csv` and
in `manifest.json`; see the [original dataset card](../../../docs/event-real-dataset.md).
Do not treat the project license as a new license grant for third-party source text.
