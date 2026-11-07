# Contract6 four-source DeepSeek smoke ? 2026-11-01

Run after pushing implementation b707f81, with explicit paid authorization.
Model: deepseek/deepseek-v4.1-flash. Four unchanged authentic PPP sources in two
chronological cohorts, fresh contract6 database, no Search or paid Judge.
PR11 was found ready and restored to Draft as requested.

| Measurement | Observed |
| --- | --- |
| Run/cohorts | partial / both partial |
| Sources represented | 4/4 |
| Persisted Events / scored | 3 / 2 |
| Calls | 12: construction4, identity3, Analysis5 |
| Time / cost | 23.390s / $0.0085547011 |
| Tokens | 34,195 input; 5,906 output |
| Rejected entries / Event drafts | 7 / 4 |
| Reported invalid-source drafts | 1 |
| Accepted evidence references checked across cohorts | 15; invalid0 |
| Unique accepted entries / invalid spans / changed original bodies | 13 / 0 / 0 |
| Frozen pair grouping | 1 true same pair, 5 true distinct pairs; no merge/split errors |
| Active Claim Types | no removed label emitted |

The capital rule remains separate from the facility; announcement and operation
share the facility Event. Capital-rule provisions remain actual, not proposed.
The Board issue retains PPP-only applicability and SBA/Board restrictions, but
combines announcement/current facts and future implementation in actual entries.
Thus binary grouping/source integrity passed this sample, while temporal semantics
and end-to-end analysis did not fully pass.

## Input, prompt and output format

The client sends OpenAI-compatible chat/completions JSON: model, messages
(system=stage prompt plus OUTPUT FIELD CONTRACT and strict schema; user=serialized
context), stream=false, max_tokens, and response_format=json_schema with strict=true.
OpenRouter reasoning is disabled. W3 has max_tokens4096 and Analysis2048.
No credentials are in the context or saved request examples.

W3 user context keys: group, sources, candidates, source_id, title, text.
The call processes one current source chunk, although grouped peer excerpts are
also visible. EventConstructionDraft returns complete plus events, each containing
title, identity, category, source_id, what_changed and existing_event_id. Embedded
entries contain text, claim_type, evidence_quote and optional numbers. Python owns
stable Event IDs and evidence offsets.

In the Board call the current source is A51f01aa4343180ebcabf4994, while peer
A997d7e0ce4426cfbd9467221 contains the PPPLF operational report. The prompt says
?Peer excerpts provide context only? and ?source_id must equal supplied source_id?.
Nevertheless the first returned Event describes the peer's PPPLF operation while
using the Board Article ID. Its three quotes occur in the peer, not current text;
all three entries and the unusable draft are rejected. The valid Board draft
survives. Related cross-source extra drafts explain all7 discarded entries and
all4 rejected drafts in this run. This is context leakage, not invented original
source evidence being accepted.

Analysis user context keys: current_event, reference_time, treasury_context,
source_times and allowed_values. The repair call adds repair and rejected_response.
Output: why_it_matters, what_to_do, impact/relevance/urgency, reasoning,
primary_source_id, primary_evidence_quote, primary_claim_type, dimension bases and
has_treasury_action_or_constraint. Only a validated result becomes a persisted Score.

Board source_times supplies 2020-04-17T00:00:00+00:00 and reference_time is
2020-04-20T01:00:00+00:00. Both outputs say the announcement was published
2020-04-17. This is supported metadata. Yet Analysis's generic numerical subset
check extracts 17 from the prose date and not from the ISO timestamp (17 immediately
followed by T fails its numeric token boundary). Offline replay confirms both
attempts fail with unsupported numerical claims. The second failure leaves
score=null and analysis arrays empty, with an explicit processing error.
This is a confirmed date/numeric-validation false positive, not a provider outage
or a failure caused by removing the Claim Type value. No runtime fix or paid rerun
was performed after this observation.

The rejected Analysis also states no funding/FX/settlement impact too categorically;
that conclusion is not established by public sources. It is not in final output.
The accepted Board entries mix actual announcement/SBA clarification with future
Board implementation. A forecast entry in the facility also uses present-tense
?helps increase? rather than an explicit expected-effect description. These are
remaining semantic problems, despite valid enum values.

## Reproduction and artifacts

`uv run --no-sync python -m evaluation.mvp --selection smoke --model
deepseek/deepseek-v4.1-flash --output outputs/claim-type-v6-deepseek-smoke-20261101`

The local output directory contains report.json, receipts.json, both cohort JSONs,
a fresh news-mvp.sqlite and primary_replies with actual contexts/parsed outputs.
request_examples contains board-construct, board-analysis-first and
board-analysis-repair request.json/response.json/prompt.txt files. Request bodies
were reconstructed offline from saved contexts and unchanged production client
code using MockTransport; they are not raw provider wire captures.

Offline fixed-reference metrics are in
outputs/claim-type-v6-deepseek-semantic-20261101/semantic-report.json. The frozen
anchor matcher reports5/9 status correctness including missing,5/7 among matched
unambiguous references,9/11 anchor coverage and category agreement9/9 matched
references. Two ?missing? announced references are anchor-matcher misses: the
facility plan is present as announced with different wording, and the PPP-only
restriction is present with a shorter paraphrase. These are not independently
verified recall estimates. Two matched Board references are actual instead of the
reference announced. Additional proposal/future-effective reference sources were
not exercised. No independent scoring/summary accuracy estimate or paid Judge.
