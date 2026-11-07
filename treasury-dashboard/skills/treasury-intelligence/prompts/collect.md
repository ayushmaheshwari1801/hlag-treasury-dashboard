# W1: Collect sources and prepare evidence

Within the run's scope, use available host search and reading tools to find public
information. If the user supplies article text, analyze it first. If tools or
sources are unavailable, state what is missing; do not invent search results.
Monitoring scope guides searches, but does not prove company exposure in a location.

Prefer original announcements and reliable reporting. For each article record its
title, URL, publisher, published_at (unknown if unavailable), discovered_at, how the
body was accessed, and the portion read. Number the passages actually saved in
sequence so they can be cited by range. A headline alone does not establish that
a measure is effective, an event is resolved or a source has authority.

Check coverage across Regulatory, Macroeconomic and Industry. Distinguish
syndication from independent follow-up; a shared topic does not justify removing
an article. Record search terms, date ranges, reading failures and reasons for
excluding out-of-scope sources. Read at most five articles per batch. By default,
perform one search group covering the three categories, with at most two queries
per category, then one targeted follow-up if an important public question remains.
Record a different scale requested by the user; do not silently search without limits.

Populate the source section of [article records](../templates/article.md) and
describe this batch's coverage. Analysis and scoring belong to the next stage.
All examples below are synthetic and cannot serve as real news sources.

## Example 1 — Regulatory

Input: An official announcement says new cross-border payment-information
requirements are planned to take effect in five days, and supplies both the
announcement date and implementation date.
Output: Record both dates, the issuing authority and announcement URL; mark it
as a Regulatory candidate. Preserve the source's planned/adopted status. Until
the operative terms have been read, do not claim universal applicability or
that payments are already blocked.

## Example 2 — Macroeconomic

Input: A headline says “Interest rate cut by 25bp,” but only a search snippet is readable.
Output: Record access as snippet and try once to read the original central bank
statement. If that fails, mark the article body unavailable. Do not invent the
new rate level, implementation date or company financing cost.

## Example 3 — Industry

Input: Three websites syndicate the same port announcement of a 48-hour customs
clearance delay; an independent report describes an extension.
Output: Link the original announcement and syndicated copies through source
membership. Keep the independent extension report as a separate Article. Do not
count four articles as four independent events or discard the extension as a duplicate.
