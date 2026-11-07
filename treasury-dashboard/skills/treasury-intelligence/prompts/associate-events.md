# E: Associate the same occurrence and record developments

Read [event rules](../references/event-rules.md), then the current article, its
evidence ranges, and the first/latest evidence for at most five candidate events.
An Event is one business occurrence, not a headline or broad topic. Consolidate
details about one occurrence in an article into one mention; independent occurrences
may be separate, with at most two mentions. Finish extraction and consolidation
before association. During association and semantic review, preserve the established
indexes and return a decision for each. Do not delete, merge or renumber mentions
to bypass a missing candidate.

Assess identity first, then the supported development. same_occurrence /
distinct_occurrence / uncertain is separate from confirmed / unverified / disputed.
Pending news may attach to an existing event without advancing its verified state.
Creating under the same subject requires a different business scope or a separate
occurrence.

Attach only to candidate IDs actually read for this mention. If one keyword/time
expansion still cannot resolve identity, defer. No matching candidate neither
automatically proves novelty nor requires deferral; assess the occurrence's
independence from its evidence. “May resume later” does not contradict “still
stopped,” and regulatory approval does not establish acquisition closing.

Perform one combined review of all this article's mentions and decisions: source
fidelity, occurrence identity, development meaning and actual completion. Include
same-subject creation and risky attachments, without splitting on date differences
alone. Procedural approval remains progress; actual completion requires scope,
confirmation qualification and source authority. Review cannot invent authority.

Judge whether the proposed action and development kind are supported. A justified
create can pass review without a matching existing Event; a faithfully qualified
pending_claim can pass without confirmed progress. Review action labels together
with verification, lifecycle, attribution and source ranges. Do not reject creation
for correctly identifying a distinct occurrence or defer solely because a claim is
unverified. Shared actors or locations alone still do not establish identity.

If a proposal is unsupported, revise only the rejected mention/decision pairs once,
using each mention's own source ranges and candidate IDs, then recheck them once.
Do not borrow another mention's candidates. Unresolved support remains deferred;
unavailable review or invalid evidence leaves an explicit failure, never implicit
approval. Keep the original extraction and review/repair reasons in history.
Allow at most one formatting/citation correction per review, without recursion.

Record decision (create/attach/corroborate/conflict/defer) and development kind
(new_development/corroboration/no_change/pending_claim/conflict/resolution/correction).
Explain occurrence identity, article evidence ranges and the state change in the
[event record](../templates/event.md). Preserve independent sources and syndication
relationships. Without history, say “first observation”; do not invent a change
from the previous period.

All cases below are synthetic. IDs beginning with E- are valid only in the examples.

## Example 1 — create / different_actor

Candidate E-DELTA concerns a Port Delta strike; the new article reports a Port Echo strike.
Conclusion: distinct_occurrence; create the Echo occurrence. A shared “strike”
keyword cannot merge different ports.

## Example 2 — create / different_scope

The candidate is a company's vessel financing; the new article reports an
independent terminal-equipment accident at the same company.
Conclusion: different_scope; create a new event and explain the different business
scopes of the financing transaction and operating accident.

## Example 3 — create / separate_occurrence

The October strike has ended, and an original announcement explicitly describes
“another strike” in November.
Conclusion: separate_occurrence; create the November occurrence. A date change
alone would not justify this conclusion.

## Example 4 — attach / new_development

The same Delta strike extends from 24 to 72 hours.
Conclusion: same_occurrence; attach to E-DELTA and add the extension development.
Update lifecycle to ongoing, retain occurrence identity, and cite the range
containing the original and new durations.

## Example 5 — corroborate / corroboration and no_change

An independent report confirms the same extension without further change:
corroborate and add a supporting source.
A syndicated copy republishes the original announcement through another copy:
resolve the full syndication chain and mark no_change. It adds neither independent
corroboration nor material progress.

## Example 6 — attach / pending_claim

An unverified report says “the port may reopen tomorrow.”
Conclusion: it may be a pending_claim within E-DELTA. Preserve the verified stoppage
state and record the question awaiting confirmation.

## Example 7 — conflict

Two reports attributable to the same occurrence make mutually exclusive claims
about whether operations have already resumed today.
Conclusion: same_occurrence; record conflict, both timestamps and both sources.
Do not simply accept the newer report as true.

## Example 8 — defer / uncertain

An article only says “the strike at the port,” without identifying the location
or occurrence.
Conclusion: if one expanded retrieval still cannot resolve it, defer and list
the ambiguity. Do not assign a superficially similar Event ID.

## Example 9 — attach / resolution

The operator formally announces that operations have actually resumed in the
port area covered by E-DELTA.
Conclusion: record resolution after checking subject, scope, date and source
authority. This ends the stoppage, not the user's ongoing port-risk Watch.
A plan to resume operations is insufficient.

## Example 10 — correction

An authorized source corrects its earlier announcement, changing the reported
stoppage duration from 72 to 48 hours.
Conclusion: append a correction to the same occurrence, identifying the corrected
record and reason. Do not erase prior evidence history.

## Example 11 — first confirmation / withdrawal

A confirmed report discovered later but published earlier may establish the first
verified state of an occurrence previously supported only by rumor. It cannot
roll back a later verified development. If the original news is shown to be wrong,
withdraw dependent active associations and preserve withdrawn history. A withdrawn
mention must not keep occupying the unresolved queue.
