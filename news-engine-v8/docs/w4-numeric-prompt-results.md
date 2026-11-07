# W4 optional numerical narrative prompt ? 2026-11-01

SPEC was updated before implementation. The following user-supplied paragraphs
were added verbatim to ANALYSIS_PROMPT in src/news_engine/analyze.py:

> Use numerical values only when directly supported by the supplied Event evidence. Do not introduce unsupported quantities, units, comparisons, or calculated financial impacts.
>
> Numerical values are optional in the narrative. Do not invent numbers to make Treasury implications or recommendations more specific.

No schema, scoring, validator, W3, call budget or repair behavior was changed.
Implementation commit: 40bc4a4. Both paragraphs were checked in the assembled
production prompt. Locked-environment offline suite: 53 passed in13.812s, before
paid evaluation. Logs: outputs/w4-numeric-prompt-offline-20261101.

## Authorized four-source DeepSeek retry

Model deepseek/deepseek-v4.1-flash; same four frozen authentic PPP sources and two
chronological cohorts, fresh database. Search and paid Judge were not run.

| Metric | Previous run | This run |
| --- | --- | --- |
| Status | partial | partial |
| Represented sources | 4/4 | 4/4 |
| Persisted/scored Events | 3/2 | 2/2 |
| Analysis calls / repairs / failed final analyses | 5/1/1 | 3/0/0 |
| Numerical narrative validation failures | 2 attempts, one Event | 0 |
| Invalid source drafts / total rejected drafts | 1/4 | 2/2 |
| Discarded entries | 7 | 0 |
| Source-pair false merges / false splits | 0/0 | 2/0 |
| Total calls | 12 | 10 |
| Wall seconds | 23.390 | 17.969 |
| Cost USD | 0.0085547011 | 0.005818113312 |
| Input/output tokens | 34195/5906 | 31459/4430 |

All three Analysis calls validated on the first attempt. Both final Events have
scores,20 and15, with all P/R/U explanations. There were no Analysis errors.
The existing ISO-date/numeric-token bug was not fixed by this prompt addition;
it simply was not triggered in this run.

Both cohorts remain partial because W3 returned an extra Event referencing a
peer Article instead of the current Article; those two drafts were rejected.
Four sources remain represented. W3 binary matching merged the separate capital
rule into the facility Event, causing two false source-pair merges under frozen
labels. The facility announcement and operational report still share an Event.
W4 was the only code changed; W3 variation between single stochastic runs cannot
be attributed to the W4 prompt.

Both final why_it_matters and what_to_do arrays are empty. Score explanations
still make overly categorical no-impact/no-action statements about Hapag-Lloyd.
Board future implementation is mixed with actual SBA clarification in an actual
entry. These remain semantic limitations, despite passing output validation.

Accepted evidence references checked across cohorts:17, invalid0. The offline
semantic evaluator checks11 unique accepted entries, invalid spans0 and original
article mismatches0. Frozen anchor metrics report status4/9 including missing,
4/5 among matched unambiguous references, anchor coverage7/11 and category
agreement7/7 matched references (previously5/9,5/7,9/11 and9/9 respectively).
The matcher requires phrase containment and misses supported paraphrases, so these
are anchor diagnostics, not independently adjudicated accuracy/recall. No removed
Claim Type or proposed false positive was emitted. Two additional reference-only
proposal/future-effective sources were not exercised.

Run artifacts: outputs/w4-numeric-prompt-deepseek-20261101/{report.json,
receipts.json,2020-04-06.json,2020-04-13.json,news-mvp.sqlite,primary_replies/}.
Offline semantic report: outputs/w4-numeric-prompt-semantic-20261101/semantic-report.json.
Previous artifacts and reports were preserved for this comparison.

Reproduce with a fresh directory:

```
uv run --no-sync python -m evaluation.mvp --selection smoke --model deepseek/deepseek-v4.1-flash --output outputs/w4-numeric-prompt-deepseek-new
```

The runner exits1 because the result is partial. No automatic extra paid rerun.
