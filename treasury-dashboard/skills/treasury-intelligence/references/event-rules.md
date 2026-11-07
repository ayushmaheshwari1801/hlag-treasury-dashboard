# Events, evidence and time

**An Event is an occurrence with a clear subject and business scope that can be
tracked over time as the same matter. A Development is an update within that
occurrence; Article/Mention provides its supporting evidence.**

A Watch is a user's ongoing monitoring goal and may cover multiple Events.
`general` is a category for items that match no specific Watch, not an event.
Do not turn an unbounded topic such as “shipping markets” or “a company” into one Event.

## Identity and state

Determine identity from the subject, underlying occurrence/business scope,
location/jurisdiction and explicit occurrence time. Announcement, extension,
changed terms, launch and resolution may belong to one occurrence. A separate
strike, financing facility or independent transaction under the same subject
must remain separate. Title words or phase changes alone do not justify a new Event.

Identity is same_occurrence, distinct_occurrence or uncertain. Assess credibility
separately: verification is confirmed/unverified/disputed; lifecycle is
announced/ongoing/resolved/unknown. Here, confirmed means supported by current
evidence, not that the agent has independently established objective truth.

Resolution requires actual completion **within the Event's business scope**:
an acquisition closing or being abandoned, port operations resuming, and so on.
Regulatory approval/prohibition, negotiations ending or planned resumption does
not automatically resolve an acquisition or strike. Check the original material
and whether its source has authority to announce completion. If that cannot be
verified, retain uncertainty; the word “official” is not a substitute for checking.

## Time fields

| Field | Meaning |
| --- | --- |
| published_at | Actual article publication time; unknown if absent, never replaced by search time |
| discovered_at | When the source was read or received in this run |
| event_at | Evidence-supported occurrence/start time; may be unknown, and a postponement need not change identity |
| development_at | Explicit time of this development; may be unknown |
| known_at | When this development was first accepted; preserve it on continuation |
| reference_time | Fixed scoring reference time for this analysis, including timezone |

Preserve date-only precision rather than inventing an hour. Compare times in
consistent timezones; an unclear deadline cannot establish urgent action. Exclude
sources unknown at the cutoff when reading historical reports. Facts collected
today about past occurrences must be labeled retrospective, not presented as
known at the time. Earlier confirmed evidence may establish the first verified
state of an unverified event; it must not roll back a later verified development.

## Keyword retrieval and time ordering

Load the event index before retrieval. Build keywords from the article's subject,
explicit aliases, business scope, location and instrument/transaction identifiers.
Take the union of explicit-identifier, subject/location and factual-keyword
matches. The normal scope includes events with material progress in the last
90 days plus unresolved events; explicit historical references may include older
archived occurrences. Use only versions and evidence known by the cutoff.

Rank exact identifier matches first, then subject+location, subject alone, and
the number of distinct informative action/fact matches. For ties, use distance
from a compatible explicit occurrence time; otherwise use material-evidence
recency, then stable ID. Repeated words, generic terms and source counts do not
increase relevance. Keep at most 20 matches and truncation counts for audit.
By default, supply the top five candidates to association, reading each one's
first anchor and latest evidence. Expand keywords/time range once if no match
is found or identity remains uncertain; do not retrieve recursively. These are
agent operating rules, not an assumption that BM25, a vector database or a
programmatic retrieval service exists.

Each mention in an article has its own candidate list. Reference only candidate
IDs whose evidence has been read for that mention; do not automatically borrow
another mention's candidates. If needed, explicitly read the candidate's evidence
first, then assess identity. Keep uncertain when verification is not possible.
For every same-subject creation, explain the different scope or separate occurrence.

## Evidence and history

Source records retain URL, title, publication/receipt times, readable text ranges
and corresponding paragraph/section/page references. A citation may be
`A001 §2–3`, linked to the actual source URL. Paragraph numbers label the saved
text excerpts; do not claim the original webpage supplied them. Do not require
word-for-word matching of model explanations or character-offset calculations.
Quoted text must actually occur in material read; label paraphrases as summaries.
Disclose snippet-only access instead of treating it as the full article.

Follow syndication chains to the original news, with cycle detection. Repeated
syndication is not independent confirmation. A new Article may corroborate an old
development without adding material progress. Preserve the history and reasons
for corrections/withdrawals while invalidating unsupported current links.
Withdrawal is different from an unresolved association. No new reporting is not
evidence that an event has ended.

Once a new body revision at the same URL is analyzed before the cutoff, retain the
old revision's deferred association only as history; it no longer counts as current
pending work. A deferred replacement still counts. An unprocessed revision or one
that appears after the cutoff cannot erase the old revision's uncertainty at that time.

Revision replacement or source correction must also clear current Watch–Event and
Article–Event links, not just update event text. Newly accepted associations may
restore links. When record times are equal, use saved order while preserving history.
Withdrawn non-material sources must also leave current citations, retrieval evidence
and pending-claim lists. Do not remove an article link while still using its text as
current support. Retain the first valid anchor and recent evidence; never fill an
empty evidence list with withdrawn excerpts.

Reading a pending claim does not establish a verified state. Later authoritative
confirmation may advance state even with the same wording, after time and
scope-completion checks, and remove the corresponding active pending claim while
preserving its history. Deduplicate only a development that already established
verified state, even after its evidence leaves the bounded display window.

A confirmed authoritative correction is an exception: if it retracts a later
state and restores an earlier one, do not ignore it merely because its wording
appeared before. Compare the correction against the current verified statement
and lifecycle; if that is already the current state, only add evidence. Older
evidence still cannot roll back a newer state. Preserve pre-correction snapshots.
A material authoritative correction also prevents retracted states from blocking
later progress. For example, after an ending announcement is retracted, a later
authoritative announcement of actual completion may resolve the event even with
identical wording, once time and actual-completion checks pass.
