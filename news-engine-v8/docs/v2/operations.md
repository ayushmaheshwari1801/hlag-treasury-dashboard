> Historical reference only. Current runtime, commands and acceptance are defined in [SPEC.md](../../SPEC.md).

# Major Change V2 operations

Major Change is the only runtime on `refactor/major-change-v2`. Deployment acceptance remains gated by
real-news quality acceptance. Existing unmarked databases are rejected before mutation.

## CLI

From `news-engine/`, configure the existing `.env` providers, then run:

```sh
uv sync --frozen --extra evaluation
uv run python -m news_agent.orchestrator brief
```

Defaults: `data/major-change-v2.sqlite`, `outputs/v2/`. The workflow reuses Exa
collection and the LLM client, then executes Changes, Event resolution, R5 scoring
and reviewed reporting in one physical Run with stage checkpoints.

Saved intake is a JSON array of existing ArticleInput objects (`url`, `title`,
`body`, `source`, optional `published_at`):

```sh
uv run python -m news_agent.orchestrator brief --articles intake.json --skip-collection --week-start 2026-10-19
uv run python -m news_agent.orchestrator brief --skip-collection --week-start 2026-10-19 --resume RUN_ID
```

Weeks start on Monday and must be complete. Reports record their actual
observation `as_of`; publication and occurrence dates remain distinct. Resume
with identical collection flags, config, model and code, without `--articles`.
Completed chunks, partial accepted siblings, associations and scores survive
failure. Changed policies require a new Run. Credit exhaustion stops new calls.

## HTTP and viewer

```sh
uv run python -m news_agent.api --database data/major-change-v2.sqlite --outputs outputs/v2/api
uv run streamlit run streamlit_app.py
```

Use POST `/api/v2/jobs` with `Idempotency-Key`, poll GET, GET `result_url`, and
POST `/{id}/resume` with `expected_attempt`. Authentication, single-worker locking,
durable attempts and restart recovery reuse the existing service. Set
`NEWS_API_TOKEN` for public binding and send its bearer token. V2 additionally
accepts `skip_collection: true`. Schedule CLI writes separately from HTTP workers.

V1 job-control aliases share the same executor; retired V1 result routes are not served. V2 results negotiate by route and identify
`pipeline: major-change-v2`, result schema 2, sources schema 5, Events schema 1,
report schema 4. No Fact/Candidate adapter is used.

Select `outputs/v2` or an HTTP job's output directory in Streamlit. Sources show
numeric/directional/status Changes and exact citations; null numeric values stay
qualitative. Reports show matching Event versions and scores, reviewed Treasury
pathways/checks, uncertainty and coverage gaps. The viewer makes no model calls.

Accepted chunks, Event versions, associations, completed scores and reports are
immutable. New source revisions suspend superseded evidence. Opening or
implementation is ongoing, not resolution. Deadline changes create assessment
revisions for the same Event version; failed or future assessments cannot fall
back silently to an older score.

## Verification

For current stage reasoning, bounded extraction concurrency and the separate
72-source extraction matrix, see the former performance report (removed on request).
The current requested model is `google/gemini-3-flash-preview`; prior DeepSeek
replays below are historical comparisons, not the production model selection.

The earlier DeepSeek live replay used V2 only and DeepSeek for extraction,
association, scoring, reporting and their reviews. Set
`LLM_MODEL=deepseek/deepseek-v4-flash` in the local environment. Use a fresh output
directory for each changed prompt, model or execution policy; do not resume paid
checkpoints under changed inputs. For the DeepSeek reasoning replay:

```sh
uv run python -m evaluation.major_change_benchmark --selection smoke --model deepseek/deepseek-v4-flash --reasoning --timeout 900 --output outputs/v2-deepseek/smoke-new
```

Run the full selection in another fresh directory after examining smoke failures.
Earlier generated rewrite runs were removed at the user's request; the original
V1 database and authentic evaluation corpus were preserved. New failed executions
remain recorded and count in evaluation denominators.

```sh
uv run python scripts/check_major_change_v2.py
uv run python scripts/check_major_change_workflow.py
uv run python benchmark.py --profile offline
uv run python -m evaluation.major_change_benchmark --selection smoke --output outputs/v2-eval-smoke
uv run python -m evaluation.major_change_benchmark --selection full --output outputs/v2-eval-full
```

On Windows set `PYTHONUTF8=1` for historical readers. Corpus validation uses
portable paths and LF hashing for Git's CRLF checkout of original JSON archives;
source TXT/CSV hashes and labels remain unchanged.

Paid evaluations retain failures and missing-source diagnostics. Event labels
cannot measure MajorChange precision/recall. V2 source replay omits W1 calls;
its totals cannot establish a full-flow reduction against V1's service benchmark.
Independent MajorChange recall and numeric correctness remain unmeasured until
separate labels are adjudicated. Critical missing occurrences block performance claims.

## Rollback

Keep V1 deployment/database untouched during acceptance. Stop this service and check out the V1 baseline in a separate deployment, then restart it
against its original database and output paths. Before cutover, stop writers and
use SQLite backup for consistent copies of both databases. Preserve baseline
`c0fe0b12257349bf5051bd1159ab63d29ce4ea69`; never migrate accepted V1 rows in place.
