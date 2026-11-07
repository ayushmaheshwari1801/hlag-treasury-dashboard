"""Read and explicitly collect contract-8 Article/Event results. No legacy pipeline imports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class V8Score(Strict):
    impact: int = Field(strict=True, ge=0, le=5)
    relevance: int = Field(strict=True, ge=0, le=5)
    urgency: int = Field(strict=True, ge=0, le=5)
    total: int = Field(strict=True, ge=0, le=100)
    reasoning: dict[str, str]

    @model_validator(mode="after")
    def arithmetic(self):
        if self.total != 8 * self.impact + 7 * self.relevance + 5 * self.urgency:
            raise ValueError("Invalid v8 R5 arithmetic")
        if set(self.reasoning) != {"impact", "relevance", "urgency"} or any(not v.strip() for v in self.reasoning.values()):
            raise ValueError("Missing v8 score reasoning")
        return self


class V8Article(Strict):
    url: str
    title: str
    text: str = Field(min_length=1)
    publisher: str | None = None
    published_at: AwareDatetime | None = None
    article_id: str = Field(pattern=r"^A[A-Za-z0-9_-]+$")
    discovered_at: AwareDatetime
    normalized_url: str
    content_hash: str


class V8Event(Strict):
    event_id: str = Field(pattern=r"^E[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1)
    category: Literal["regulatory", "macroeconomics", "industry"]
    article_ids: list[str] = Field(min_length=1)
    summary: str = Field(min_length=1)
    score: V8Score | None = None
    why_it_matters: list[str] = Field(default_factory=list, max_length=2)
    what_to_do: list[str] = Field(default_factory=list, max_length=2)


class V8Error(Strict):
    stage: str
    item_id: str | None = None
    code: str
    message: str


class V8Result(Strict):
    schema_version: Literal[8]
    run_id: str = Field(min_length=1)
    status: Literal["completed", "partial", "failed"]
    events: list[V8Event] = Field(default_factory=list)
    sources: list[V8Article] = Field(default_factory=list)
    errors: list[V8Error] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    overall_summary: str = ""

    @model_validator(mode="after")
    def assignments(self):
        source_ids = {s.article_id for s in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("Duplicate v8 source Article")
        owners, ids = set(), set()
        for event in self.events:
            if event.event_id in ids:
                raise ValueError("Duplicate v8 Event")
            ids.add(event.event_id)
            if not event.title.strip() or not event.summary.strip():
                raise ValueError("Empty v8 Event title/summary")
            if any(not v.strip() for v in event.why_it_matters + event.what_to_do):
                raise ValueError("Empty v8 analysis entry")
            for key in event.article_ids:
                if key not in source_ids or key in owners:
                    raise ValueError("Missing or multiply assigned v8 Article")
                owners.add(key)
        return self


# UI relevance hints only. Keywords never create magnitudes, confidence or adjustments.
TERMS = {
    "freight": ("freight", "container", "red sea", "suez", "port", "shipping", "tariff"),
    "bunker": ("bunker", "fuel", "oil", "vlsfo", "hsfo", "scrubber"),
    "interest": ("interest", "sofr", "estr", "central bank", "federal reserve", "ecb", "debt", "refinanc"),
    "fx": ("exchange rate", "currency", "foreign exchange", "capital control", "devaluation"),
}


def modules_for(text: str) -> list[str]:
    return [key for key, terms in TERMS.items() if any(
        re.search(r'\b' + re.escape(term) + (r'\w*' if term == 'refinanc' else r's?\b'), text.lower())
        for term in terms)]


def event_rank(event, sources):
    times = [datetime.fromisoformat(sources[key]['published_at'].replace('Z','+00:00')).timestamp()
             for key in event['article_ids'] if sources[key].get('published_at')]
    return (-event['score']['total'], -max(times, default=float('-inf')), event['event_id'])


class NewsV8Reader:
    def __init__(self, directory: str | Path | None):
        self.dir = Path(directory) if directory else None
        self.lock = RLock()

    def latest(self) -> tuple[dict | None, str | None]:
        if not self.dir or not self.dir.exists():
            return None, None
        files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        errors = []
        for path in files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                if raw.get("schema_version") != 8:
                    continue
                return V8Result.model_validate(raw).model_dump(mode="json"), path.name
            except (ValueError, OSError, AttributeError) as exc:
                errors.append(f"{path.name}: invalid v8 export ({type(exc).__name__})")
        return None, "; ".join(errors) or None

    def cumulative(self) -> tuple[dict | None, str | None]:
        """Snapshot the active database's latest Event versions, without collecting news."""
        configured = os.environ.get("NEWS_V8_DATABASE")
        if not configured or not self.dir:
            return self.latest()
        database = Path(configured).resolve()
        if not database.is_file():
            return self.latest()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
            db.execute("BEGIN")
            marker = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
            if not marker or marker[0] != "news-engine-mvp-8":
                raise ValueError("The active news database is not contract 8")
            events = [json.loads(row[0]) for row in db.execute("SELECT data FROM events ORDER BY event_id")]
            if not events:
                return self.latest()
            wanted = {aid for event in events for aid in event['article_ids']}
            sources = [json.loads(row[0]) for row in db.execute("SELECT data FROM articles ORDER BY article_id")]
            sources = [article for article in sources if article['article_id'] in wanted]
            runs = [row[0] for row in db.execute("SELECT run_id FROM runs WHERE status IN ('completed','partial') ORDER BY rowid")]
        # Identical knowledge keeps the same snapshot/cache identity after a cached rerun.
        identity = json.dumps({'database': database.name, 'events': events, 'sources': sources},
                              ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(identity.encode()).hexdigest()
        run_id = 'pool_' + digest
        directory = self.dir / 'pools'
        path = directory / (digest + '.json')
        with self.lock:
            if path.exists():
                return V8Result.model_validate_json(path.read_text(encoding='utf-8')).model_dump(mode='json'), 'pools/' + path.name
            result = V8Result.model_validate({
                'schema_version': 8, 'run_id': run_id,
                'status': 'completed' if all(event['score'] is not None for event in events) else 'partial',
                'events': events, 'sources': sources, 'errors': [],
                'metrics': {'kind': 'cumulative_event_pool', 'source_database': database.name,
                            'source_run_ids': runs, 'event_count': len(events)},
                'overall_summary': 'Cumulative monitoring pool. ' + '; '.join(event['title'] for event in events)
            }).model_dump(mode='json')
            directory.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix('.pending')
            pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            pending.replace(path)
        return result, 'pools/' + path.name

    def saved_run(self, run_id: str) -> dict | None:
        if not self.dir or not self.dir.exists():
            return None
        for path in [*self.dir.glob("*.json"), *(self.dir / "pools").glob("*.json")]:
            try:
                result = V8Result.model_validate_json(path.read_text(encoding="utf-8-sig")).model_dump(mode="json")
                if result["run_id"] == run_id:
                    return result
            except (ValueError, OSError):
                continue
        return None

    def import_result(self, payload: dict) -> dict:
        try:
            result = V8Result.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            fields = [".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False, include_url=False)[:8]]
            raise ValueError("Choose a complete saved v8 news result. " + "; ".join(fields)) from None
        if self.dir is None:
            raise ValueError("NEWS_V8_EXPORT_DIR is not configured")
        self.dir.mkdir(parents=True, exist_ok=True)
        name = "run_" + hashlib.sha256(result["run_id"].encode()).hexdigest()[:24] + ".json"
        path = self.dir / name
        with self.lock:
            if path.exists():
                old = json.loads(path.read_text(encoding="utf-8"))
                if old != result:
                    raise ValueError("A saved v8 run_id is immutable; supply a new run")
                return result
            pending = path.with_suffix(".pending")
            pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(path)
        return result

    def collect(self, request: dict) -> dict:
        url, token = os.environ.get("NEWS_V8_URL"), os.environ.get("NEWS_API_TOKEN")
        if not url or not token:
            raise ValueError("Configure NEWS_V8_URL and NEWS_API_TOKEN to invoke the v8 workflow")
        try:
            response = httpx.post(url.rstrip("/") + "/api/news/runs", json=request,
                                  headers={"Authorization": f"Bearer {token}"}, timeout=600)
            response.raise_for_status()
            return self.import_result(response.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                try:
                    detail = exc.response.json().get("detail")
                except (ValueError, AttributeError):
                    detail = None
                if detail == "News pipeline is busy":
                    raise RuntimeError("News collection is already running. Wait for it to finish before refreshing again; current news is unchanged.") from None
                if detail == "Database or classification policy is incompatible; use a fresh MVP database":
                    raise RuntimeError("News database or classification policy is incompatible. A separate compatible news database is required; existing news is preserved.") from None
            raise RuntimeError(f"News v8 returned HTTP {exc.response.status_code}; no retry") from None
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("News v8 request failed or returned an invalid contract; no retry") from None

    def events(self) -> dict:
        data, source = self.cumulative()
        return {"available": data is not None, "schema_version": 8,
                "run_id": data["run_id"] if data else None,
                "status": data["status"] if data else "unavailable",
                "events": data["events"] if data else [], "sources": data["sources"] if data else [],
                "errors": data["errors"] if data else [], "export": source,
                "message": None if data else "No validated v8 export. Configure NEWS_V8_EXPORT_DIR or import a RunResult."}

    def _items(self, detail=False, data=None):
        if data is None:
            data, _ = self.cumulative()
        if not data:
            return []
        source_index = {article['article_id']: article for article in data['sources']}
        owners = {aid: event for event in data["events"] for aid in event["article_ids"]}
        ranked = sorted(data["events"], key=lambda e: -(e["score"]["total"] if e["score"] else -1))
        ranks = {event["event_id"]: i + 1 for i, event in enumerate(ranked)}
        items = []
        for article in data["sources"]:
            event = owners.get(article["article_id"])
            if event is None:
                continue
            score = event["score"]
            item = {"id": article["article_id"], "title": article["title"], "summary": event["summary"],
                    "category": event["category"], "published_at": article["published_at"],
                    "score": score["total"] if score else None, "rank": ranks[event["event_id"]],
                    "score_scope": "event", "review_status": "v8_analysis" if score else "unscored",
                    "ai_analyzed": score is not None, "factually_verified": False,
                    "unverified": True, "source": article["publisher"], "url": article["url"],
                    "modules": modules_for(event["title"] + " " + event["summary"]), "areas": [],
                    "event_id": event["event_id"], "event_title": event["title"], "event_labels": [event["title"]],
                    "article_ids": event['article_ids'], "source_count": len(event['article_ids']),
                    "treasury_pathways": event["why_it_matters"], "scenario_tests": [],
                    "run_id": data["run_id"], "schema_version": 8}
            if detail:
                score_values = ({key: score[key] for key in ('impact', 'relevance', 'urgency')} if score else None)
                reasons = dict(score['reasoning']) if score else {}
                if score_values:
                    for legacy, current in [('potential_impact','impact'), ('company_relevance','relevance'), ('urgency_materiality','urgency')]:
                        score_values[legacy] = score[current]
                        reasons[legacy] = score['reasoning'][current]
                event_sources = [{**source_index[aid], 'source': source_index[aid]['publisher']}
                                 for aid in event['article_ids']]
                item.update(text=article["text"], original_article=article,
                            facts=[], signals=[], sources=event_sources, scores=score_values, event_score=score,
                            score_reasons=reasons,
                            recommended_actions=event["what_to_do"], candidate_assumptions={},
                            note="Context only. Event analysis and R5 scoring are AI outputs, not factual verification or forecast confidence. Original article text remains source evidence; a separate forecast creates estimated what-if inputs.")
            items.append(item)
        return sorted(items, key=lambda a: -(a["score"] if a["score"] is not None else -1))

    def articles(self, limit=20, module=None):
        data, source = self.cumulative()
        items = self._items(data=data)
        if module:
            items = [item for item in items if module in item["modules"]]
        return {"available": data is not None, "items": items[:limit], "total": len(items),
                "as_of": max((s["discovered_at"] for s in data["sources"]), default=None) if data else None,
                "status": data["status"] if data else None, "coverage_gaps": data["errors"] if data else [],
                "export": source, "message": None if data else "No saved v8 news result found."}

    def tracker(self, limit=20, module=None):
        """One Event row, retaining a real primary Article ID for the original drawer."""
        data, source = self.cumulative()
        if not data:
            selection = self._scenario_events(None)
            return {"available": False, "items": [], "total": 0, "as_of": None,
                    "status": None, "coverage_gaps": [], "export": source,
                    "message": "No saved v8 news result found.", "item_scope": "event",
                    "scenario_events": [], "scenario_selection": {k:v for k,v in selection.items() if k!='events'}}
        articles = {item['id']: item for item in self._items(data=data)}
        source_index = {article['article_id']: article for article in data['sources']}
        items = []
        for event in data['events']:
            primary = articles[event['article_ids'][0]]
            if module and module not in primary['modules']:
                continue
            citations = [{key: source_index[aid].get(key) for key in
                          ('article_id','url','title','publisher','published_at','discovered_at','normalized_url','content_hash')}
                         for aid in event['article_ids']]
            for citation in citations:
                citation['source'] = citation['publisher']
            items.append({**primary, 'article_title': primary['title'], 'title': event['title'],
                          'sources': citations, 'citations': citations})
        items.sort(key=lambda item: (-(item['score'] if item['score'] is not None else -1), item['event_id']))
        selection = self._scenario_events(data)
        return {"available": True, "items": items[:limit], "total": len(items), "item_scope": "event",
                "scenario_events": selection['events'], "scenario_selection": {k:v for k,v in selection.items() if k!='events'},
                "as_of": max((article['discovered_at'] for article in data['sources']), default=None),
                "status": data['status'], "coverage_gaps": data['errors'], "export": source, "message": None}

    def _scenario_events(self, data):
        categories = ('regulatory','macroeconomics','industry')
        counts = {category: 0 for category in categories}
        excluded = {category: 0 for category in categories}
        available = {category: 0 for category in categories}
        chosen = []
        sources = {article['article_id']:article for article in data['sources']} if data else {}
        for category in categories:
            candidates = [event for event in data['events'] if event['category']==category and event['score'] is not None] if data else []
            available[category] = len(candidates)
            excluded[category] = sum(event['category']==category and event['score'] is None for event in data['events']) if data else 0
            for event in sorted(candidates, key=lambda event:event_rank(event,sources))[:2]:
                primary = sources[event['article_ids'][0]]
                cited = [{**{key:sources[aid].get(key) for key in
                            ('article_id','url','title','publisher','published_at','discovered_at','normalized_url','content_hash')},
                          'source':sources[aid]['publisher']} for aid in event['article_ids']]
                known = [article['published_at'] for article in cited if article['published_at']]
                published = max(known, key=lambda value:datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()) if known else None
                chosen.append({**event, 'id':primary['article_id'], 'primary_article_id':primary['article_id'],
                               'event_title':event['title'], 'article_title':primary['title'],
                               'source_count':len(event['article_ids']), 'source':primary['publisher'],
                               'published_at':published, 'sources':cited, 'citations':cited, 'score_scope':'event',
                               'run_id':data['run_id'], 'modules':modules_for(event['title']+' '+event['summary'])})
                counts[category] += 1
        gaps = [{'category':category,'required':2,'available':available[category],'missing':2-counts[category]}
                for category in categories if counts[category]<2]
        return {'available':data is not None,'events':chosen,'category_counts':counts,'available_counts':available,
                'excluded_unscored':excluded,'gaps':gaps,'complete':not gaps,'required_total':6,
                'selection_policy':'Top two scored Events per category from the cumulative active pool by R5 total, latest source publication, then Event ID; no cross-category fill.',
                'run_id':data['run_id'] if data else None}

    def scenario_events(self):
        data,_ = self.cumulative()
        return self._scenario_events(data)

    def article(self, article_id):
        current = next((item for item in self._items(detail=True) if item["id"] == article_id), None)
        if current or not self.dir:
            return current
        # Explicit evidence links from older saved forecasts still resolve their originals.
        for path in sorted(self.dir.glob("*.json"), key=lambda path:path.stat().st_mtime_ns, reverse=True):
            try:
                data = V8Result.model_validate_json(path.read_text(encoding='utf-8-sig')).model_dump(mode='json')
                item = next((item for item in self._items(detail=True,data=data) if item['id']==article_id),None)
                if item:
                    return item
            except (ValueError, OSError):
                continue
        return None

    def brief(self):
        data, source = self.cumulative()
        if not data:
            return {"available": False, "message": "No saved v8 analysis found."}
        return {"available": True, "export": source, "run_id": data["run_id"], "status": data["status"],
                "week_start": None, "week_end": None, "version": 8,
                "executive_summary": "\n".join([
                    f"Monitor {len(data['events'])} news events; company exposure and payment timing still need confirmation.",
                    "Review the individual event estimates before changing the cash forecast; separate news scenarios must not be added together.",
                    "Where an amount is not quantified, confirm contractual terms, exposure and settlement dates.",
                    "Next: prioritize the highest-ranked news in each category and review funding or collection needs when evidence changes."]),
                "sections": [{"title": e["title"], "items": [{"summary": e["summary"],
                              "article_ids": e["article_ids"], "recommended_actions": e["what_to_do"]}]}
                             for e in data["events"]],
                "pending_claims": [], "coverage_gaps": data["errors"],
                "message": "Cumulative v8 Event analysis; not a separately generated weekly brief."}
