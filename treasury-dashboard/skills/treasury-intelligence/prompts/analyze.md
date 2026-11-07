# W2: Analyze, classify and score

Read [company context](../references/company-context.md) and the
[scoring rubric](../references/scoring-rubric.md). Analyze one article that has
been read and assign its primary category: Regulatory, Macroeconomic or Industry.
Keep one Article record, which may contain multiple facts. Identify actual
syndication; a new development on the same topic is not a duplicate. Check for new
facts and preserve proposals, forecasts and uncertainty. Record gaps when the
body or essential evidence is missing rather than forcing a complete score.

Number the saved source's sentence/paragraph segments and attach an inclusive
start/end segment range to each material fact. Keep the source article/revision
identity with that range. Paraphrase facts faithfully; do not require the model
to reproduce quotations or count character offsets. Preserve quantities, units,
attribution and uncertainty, and record missing evidence explicitly. These ranges
identify evidence within one article analysis; they do not require a separate
model call for each segment.

Score P/R/U (each 0–5) for the same primary development and Treasury channel.
Compute and recheck `8P + 7R + 5U`. If there are multiple developments, assess
them separately and choose the one with the highest total; break ties by R, P,
U, then source order. Do not combine the highest dimensions from different facts.
Use the fixed reference_time for scoring.

Write one or two article-specific sentences for each dimension: what changed,
how it affects funding needs or monitoring, and what remains unproven. Do not
copy anchor wording or say only that it “falls within monitoring scope.” Do not
invent company exposure without evidence. Keep review_status
(confirmed/pending/rejected) separate from priority scores. Explanations use the
article's sources; do not create a separate scoring facts/sourcing links panel.

Independent articles may share a review batch of up to five. Keep each article ID
and source ranges separate; never use one article as evidence for another. Process
revisions, dependent follow-ups and unapproved duplicate references in order.

Before accepting the article record, review its facts, summary, scoring reasons
and claim status together against the cited source ranges. Judge semantic support,
including equivalent units and faithful paraphrases. Do not use word-for-word
matching as the acceptance rule. A negative verdict allows one revised analysis
and one recheck; remove unsupported facts and update all dependent explanations.
If support still fails, retain a failed/pending record rather than publish it as
validated. Record review reasons separately. Source identity/ranges and weighted
arithmetic still require direct verification; the scoring rubric is unchanged.

Complete the [article record](../templates/article.md). All examples below are
synthetic, with reference date 2025-05-07. Do not copy their facts into real reports.

## Example 1 — Regulatory

Input: An adopted global shipping payment-information rule takes effect on May 12
and requires additional payee verification. No payment-channel failure is reported.
Output: Regulatory, P3/R4/U4, total 72.
P: The new verification obligation changes payment controls, but does not establish
restricted access to funds.
R: The rule explicitly applies to container shipping companies, without relying
on unknown banking or geographic relationships.
U: May 12 is five days after the reference date, so the verification process needs
to be prepared before implementation.

## Example 2 — Macroeconomic

Input: The USD policy rate is cut by 25bp, effective immediately, with no information
about repricing the company's debt.
Output: Macroeconomic, P2/R4/U2, total 54.
P: The 25bp change affects USD funding-forecast assumptions, without creating a
new financing obligation.
R: USD rates directly inform company funding monitoring, but the report does not
supply the terms of its existing borrowing.
U: Immediate effect warrants updating the benchmark; without a company transaction
or payment deadline, it is not an urgent payment task.

## Example 3 — Industry

Input: Port Delta reports an additional 48-hour customs clearance delay through
May 10, without identifying any affected company service.
Output: Industry, P2/R3/U2, total 47.
P: The delay may affect collection timing and operating-cost forecasts; actual
losses cannot be inferred.
R: Container shipping provides an operating-to-cash channel, but the company's
actual affected services are unconfirmed.
U: May 10 is an operational deadline, with no corresponding Treasury payment or
funding deadline.
