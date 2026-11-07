# W4: Produce a readable, traceable brief

Use only completed article/event records and their evidence. Produce Markdown
using the [report template](../templates/report.md); follow the user's language
by default. Do not introduce unread facts, rewrite existing signals or
automatically rescore at this final stage. Explain omissions, corrections and exclusions.

For Events spanning weeks and linked to this week's news, read saved prior-period
support as needed, independently of Watch membership. Select at most 20 relevant
events by news ranking and stable Event ID, with at most 100 total Watch/Event
supporting articles. Read them in batches, preserving the weekly cutoff for known
and processed evidence and the publication-time restrictions. Explicitly report
over-limit or unread support as gaps; unrelated historical events do not create
this week's gaps. Withdrawn sources must not remain in current evidence.
For historical reports, event analysis completed after the cutoff cannot establish
what was known before it. Label unavailable historical coverage or retrospective
analysis; an empty event section does not prove there were no events at the time.

Structure: period/reference time/coverage → two- or three-sentence summary → news
priority table → event progress table → three-dimensional scoring explanations
for key articles → public follow-up items/deadlines and coverage gaps. Display at
most ten news items by default, record total and omitted counts, and use an appendix
for the rest if needed. Do not let a “Top 10” label hide processing failures.

Sort by descending total score, descending publication time, then stable Article
ID. Mark unscored items and unknown times separately; do not invent a default date.
Explain the actual article content rather than repeating the rubric. Put source
links in news/event rows and evidence citations, without an additional scoring
facts/sourcing panel.

Pair icons with text: 🔄 ongoing, ✅ evidence-supported resolution, ❓ awaiting
confirmation, ⚠️ conflict or coverage gap. An occurrence's success or completion
does not establish that the company has no remaining risk. Add a timeline or trend
chart only when enough time/numeric observations support it and the host can render
it natively. Do not run plotting programs or invent axes or downloadable files.

Report completeness as complete (requested steps handled), partial (valid content
with gaps), or blocked (insufficient sources/tools to begin substantive analysis).
These labels do not represent factual accuracy. All examples below are synthetic.

## Example 1 — complete in the requested input scope

The user supplies three complete articles, one per category, and requests
attachment-only analysis.
Return three sources, a ranking table and relevant events, labeled “complete:
the three supplied articles were analyzed; no broader web collection was performed.”
Do not claim that all important news for the week was covered. Keep numeric
changes in a table when there is only one observation.

## Example 2 — partial with conflicting evidence

One of five article bodies is inaccessible, and two others conflict about the
same port reopening.
Return the four completed records, ❓/⚠️ event status, the inaccessible source
and outstanding verification questions. Keep the result partial rather than
searching indefinitely for a green status. Do not turn conflicting reopening
reports into a confirmed resolution.

## Example 3 — blocked without sources

The host has no web tools, and the user supplies no article text but asks for
“today's major news.”
Return blocked, explain that no usable material is available, and request article
text or readable content from links. Do not invent a news table, scores or trend
chart, or claim that background work continues.
