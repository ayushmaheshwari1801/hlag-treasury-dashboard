# W3: Extract major changes

Read the current article's W2 summary and supporting source ranges. Return the
single most important change; retain a second only if it is independent and
equally important. Do not list every number, date or background statistic.

Fields: indicator, level (numeric/directional/factual_only), value, unit, change,
direction, article_id and evidence_range. Leave missing fields unknown or not
applicable; do not infer company losses. Use numeric only for explicitly supported
values. Comparisons require the same indicator, scope, baseline and compatible
units. If the source only says “increased by 25bp,” keep that change without
back-calculating an unknown prior value. Any necessary calculation must be simple,
supported by evidence and checked. Preserve approximations, ranges, assumptions
and negation instead of turning them into false precision.

Use paragraph, section or page ranges to identify supporting evidence. Scoring
explanations do not require word-for-word matching. Direct quotations must come
from text actually read; label paraphrases as summaries. Return an empty list
with a reason when there is no reliable major change.
Write the output into the [article record](../templates/article.md).
All examples below are synthetic.

## Example 1 — numeric

Input A001 §2: “The same daily-average measure rose from 100 to 120 tonnes.”
Output: indicator=daily-average measure, level=numeric, value=120, unit=tonnes,
change=+20 tonnes, direction=increase, evidence=A001 §2.
If the source does not establish a comparable basis, do not calculate change.

## Example 2 — directional

Input A002 §3: “Waiting time increased by approximately two to three days.”
Output: level=directional, direction=worsening; preserve “approximately two to
three days” in the summary. Do not supply an exact scalar in value/unit/change.
Evidence=A002 §3.

## Example 3 — factual_only

Input A003 §1–2: “The commission published a proposed payment-approval rule;
it has not been adopted.”
Output: level=factual_only, indicator=publication of a proposed payment-approval
rule (not adopted), with no numeric value or direction. Do not describe an
implemented payment restriction. Evidence=A003 §1–2.

## Example 4 — no signal

Input A004 is a directory of links, or only says that something “may decline in
the future” without a clear, usable new fact.
Output: An empty major-change list, explaining that the directory lacks article
text or the material contains only a forecast without a concrete basis.
