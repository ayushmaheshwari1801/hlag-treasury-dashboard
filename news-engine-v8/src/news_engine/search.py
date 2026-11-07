"""One bounded query plan, Exa discovery and a successful-query watermark."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from .models import ArticleInput, Error, StrictModel, Category


DEFAULT_GEOGRAPHIES = ['Global', 'North America', 'Europe', 'Asia-Pacific', 'Middle East', 'Latin America', 'Africa']

class SearchQuery(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    topic: Category
    geography: str = Field(min_length=1, max_length=120)

    @field_validator('query', 'geography')
    @classmethod
    def meaningful(cls, value):
        value = ' '.join(value.split())
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Empty or invalid search attribute')
        return value


class QueryPlan(StrictModel):
    queries: list[SearchQuery] = Field(min_length=1, max_length=12)


PROMPT = """Plan a small public-news search for Hapag-Lloyd Treasury monitoring.
Cover material regulatory, macroeconomic and shipping/industry developments with
potential funding, FX, cash-planning, settlement or compliance implications.
Return at most max_queries distinct concise Exa queries. Use the supplied search
history only to support incremental discovery. Do not create follow-up tasks or
seek private company data. Allocate budget dynamically across topics and geographic_scopes, using relevance,
recent history, under-covered regions and emerging risks. Include a Global discovery
query. Countries, ports, trade lanes and chokepoints are valid geographic scopes.
Do not execute the full Cartesian product. Return structured SearchQuery objects
(query, topic, geography). Geography is search intent, not proof of company exposure.
Allowed field inputs: query is a nonempty string of at most 500 characters;
topic is exactly regulatory/macroeconomics/industry; geography is a nonempty string
of at most 120 characters naming a supplied scope or a relevant country, port,
trade lane or chokepoint. Include at least one geography="Global" query. No nulls.
queries contains 1..max_queries objects (absolute cap 12), unique by normalized text.
Search only articles published within the supplied 48-hour publication window.
Return only the structured query plan."""


def search(llm, exa, store, *, max_queries=7, now=None, geographic_scopes=None, coverage=None):
    if type(max_queries) is not int or not 1 <= max_queries <= 12:
        raise ValueError("max_queries must be 1..12")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Search cutoff must be timezone-aware")
    scopes = DEFAULT_GEOGRAPHIES if geographic_scopes is None else geographic_scopes
    if (not isinstance(scopes, list) or not 1 <= len(scopes) <= 30
            or any(not isinstance(scope,str) or not scope.strip() or len(scope)>120 for scope in scopes)):
        raise ValueError('Invalid geographic scopes')
    errors = []
    try:
        plan = llm.complete(QueryPlan, PROMPT, {
            "max_queries": max_queries, "now": now.isoformat(),
            "initial_window_start": (now-timedelta(days=2)).isoformat(), "incremental_overlap_days": 1,
            "recent_queries": store.history()[-24:],
            "geographic_scopes": scopes,
        }, stage="search")
        queries = [item.query for item in plan.queries]
        if not any(item.geography.casefold() == "global" for item in plan.queries):
            raise ValueError("Global discovery query required")
        if (len(queries) > max_queries or any(not query for query in queries)
                or len({query.casefold() for query in queries}) != len(queries)):
            raise ValueError("Invalid query budget or duplicate query")
    except (ValueError, RuntimeError):
        return [], [Error(stage="search", item_id="plan", code="search_plan_failed",
                          message="Search planning failed; no queries executed")]
    if coverage is not None:
        coverage.extend(item.model_dump() for item in plan.queries)
    articles = []
    for planned in plan.queries:
        query = planned.query
        last = store.search_since(query)
        window_start = now - timedelta(days=2)
        start = min(now, max(window_start, datetime.fromisoformat(last) - timedelta(days=1))) if last else window_start
        try:
            result = exa.search(query, start=start, end=now)
        except (ValueError, RuntimeError):
            errors.append(Error(stage="search", item_id=query, code="search_failed",
                                message="Exa search failed; query watermark unchanged"))
            continue
        valid_response = True
        for index, item in enumerate(result["results"]):
            try:
                article = ArticleInput.model_validate({
                    "url": item["url"], "title": item["title"], "text": item["text"],
                    "publisher": urlsplit(item["url"]).hostname, "published_at": item.get("publishedDate"),
                })
            except (ValueError, KeyError, TypeError):
                valid_response = False
                errors.append(Error(stage="search", item_id=f"{query}:{index}",
                                    code="invalid_source", message="Search source lacks usable article text or metadata"))
                continue
            articles.append(article)
        if valid_response:
            store.record_search(query, now.isoformat(), planned.model_dump())
    return articles, errors
