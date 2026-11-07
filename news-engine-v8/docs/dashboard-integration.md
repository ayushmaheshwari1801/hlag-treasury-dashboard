# Dashboard integration ? contract 8

The synchronous CLI/HTTP runtime executes Search -> Article Grouping -> Event
Classification & Consolidation -> Scoring & Analysis -> JSON. Original Articles
and persistent semantic Event labels are the only business entities.

`POST /api/news/runs` requires `Bearer NEWS_API_TOKEN`. Supply `articles:[ArticleInput,...]`
or omit articles for Search. Settings: workers 1..8, max_queries 1..12 (default 7),
geographic_scopes, analysis_batch_size 1..4 (default 3), analysis_workers 1..8
(default 2). `GET /api/news/runs/{run_id}` reads saved output without model calls.
`GET /healthz` is public. Server owns paths/provider credentials; CORS origins are explicit.
Inspect status/errors even on HTTP 200. Errors: 401 authorization, 409 busy/incompatible
policy, 422 request validation, 503 configuration/execution unavailable.

RunResult fields: schema_version=8, run_id, status (completed/partial/failed), events,
sources, errors, metrics, overall_summary. Event fields: event_id, title, category,
article_ids, summary, score, why_it_matters, what_to_do. Each referenced Article appears
in sources with article_id, original url/title/text/publisher/published_at, discovered_at,
normalized_url and content_hash. Historical Articles associated with a returned Event
remain included. Full text is preserved, including secondary issues.

Each relevant Article has one primary Event, and every Event has at least one Article.
The current evaluated Article set determines cardinality; unrelated historical Events
never enter its denominator. Python rejects duplicate/foreign/missing assignments rather
than truncating Events. SQLite keeps immutable article_id -> event_id associations.
Category is regulatory/macroeconomics/industry; existing historical category stays stable.
No What Changed, evidence entries, Claim Types, identity fields or progression timestamps remain.

Score contains impact/relevance/urgency (strict integers 0..5), total=8P+7R+5U and
nonempty reasoning for each dimension. Hard caps are currently disabled. W4 directly
analyzes assigned original Articles and returns conditional
Treasury considerations without inventing company exposures. A new Article invalidates
analysis; identical source collections avoid rescoring. Failed Events retain usable
assignments, remain unscored and report explicit errors. Batch results bind by Event ID;
valid siblings survive, with up to two automatic validation repairs per failed Event.

Use fresh `data/news-mvp-v8.sqlite` (marker `news-engine-mvp-8`). Old files are refused
without modification. Pending proposals are technical retry metadata only. Source
classification/association and proposal consumption are atomic; writer locking and
restart recovery remain. No paid calls are needed to read saved results.

```powershell
uv run --no-sync python -m news_engine.api
uv run --no-sync python -m news_engine.pipeline --input articles.json --database data/news-mvp-v8.sqlite --output result.json
```

No application input truncation or output-token limit applies. Provider context capacity,
completion checks and bounded call/query/concurrency budgets still apply. Numerical token
support is a lightweight guard, not semantic proof. False merges/splits remain possible.
See the [example JSON](../examples/event-result.json) and [SPEC](../SPEC.md).

Automatic W4 recovery stops after success or two single-Event repair calls. With numerical source checks disabled,
number mismatches do not cause retries or force qualitative narrative. Successful siblings and
existing scores are reused; Search and classification are not repeated. Provider,
authentication, credit and transport failures stop immediately. Exhausted Events
remain explicitly unscored/partial. Actual calls, tokens and costs are recorded.
