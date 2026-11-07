"""Cumulative monitoring uses active Event state, with immutable forecast provenance."""
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from treasury_gateway.event_actions import event_actions
from treasury_gateway.forecast import ForecastAgent, ForecastRequest
from treasury_gateway.news_v8 import NewsV8Reader, V8Result
from treasury_gateway.signals import IntelligenceStore


def example():
    path = Path(__file__).resolve().parents[3] / "news-engine-v8/examples/event-result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def article(base, article_id, published="2026-11-01T10:00:00Z"):
    value = copy.deepcopy(base)
    value.update(article_id=article_id, title=f"Original report {article_id}",
                 text=f"Unchanged original source text for {article_id}.",
                 url=f"https://example.test/{article_id}", normalized_url=f"https://example.test/{article_id}",
                 published_at=published, discovered_at="2026-11-02T00:00:00Z")
    value["content_hash"] = hashlib.sha256(value["text"].encode()).hexdigest()
    return value


@pytest.fixture
def active_pool(tmp_path, monkeypatch):
    seed = example()
    events, sources = [], []
    for category in ("regulatory", "macroeconomics", "industry"):
        for index in range(3):
            aid, eid = f"A{category}{index}", f"E{category}{index}"
            source = article(seed["sources"][0], aid)
            event = copy.deepcopy(seed["events"][0])
            score = copy.deepcopy(event["score"])
            dimension = index + 1
            score.update(impact=dimension, relevance=dimension, urgency=dimension, total=20*dimension)
            event.update(event_id=eid, title=f"{category} monitoring issue {index}",
                         summary=f"Original Event summary {eid}.", category=category,
                         article_ids=[aid], score=score, what_to_do=[f"Review original advice for {eid}."])
            if eid == "Eregulatory2":
                event["title"] = "Red Sea shipping disruption"
                second = article(seed["sources"][1], "Aregulatory2second")
                sources.append(second)
                event["article_ids"].append(second["article_id"])
            sources.append(source)
            events.append(event)
    unscored = copy.deepcopy(events[-1])
    unscored.update(event_id="Eindustry_unscored", article_ids=["Aindustry_unscored"], score=None,
                    why_it_matters=[], what_to_do=[])
    events.append(unscored)
    sources.append(article(seed["sources"][0], "Aindustry_unscored"))
    db = tmp_path / "active-v8.sqlite"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO metadata VALUES('schema','news-engine-mvp-8');
            CREATE TABLE articles(article_id TEXT PRIMARY KEY,normalized_url TEXT,content_hash TEXT,data TEXT,state TEXT);
            CREATE TABLE events(event_id TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE runs(run_id TEXT PRIMARY KEY,status TEXT,data TEXT);
        """)
        connection.executemany("INSERT INTO articles VALUES(?,?,?,?,?)",
            [(a["article_id"],a["normalized_url"],a["content_hash"],json.dumps(a),"accepted") for a in sources])
        connection.executemany("INSERT INTO events VALUES(?,?)", [(e["event_id"],json.dumps(e)) for e in events])
        connection.execute("INSERT INTO runs VALUES('Rolder','completed',NULL)")
        connection.execute("INSERT INTO runs VALUES('Rlatest','completed',NULL)")
    monkeypatch.setenv("NEWS_V8_DATABASE", str(db))
    reader = NewsV8Reader(tmp_path / "exports")
    recent_ids = {"Eregulatory0", "Emacroeconomics0", "Eindustry0", "Eindustry1"}
    latest = copy.deepcopy(seed)
    latest.update(run_id="Rlatest", status="completed", events=[e for e in events if e["event_id"] in recent_ids])
    wanted = {aid for e in latest["events"] for aid in e["article_ids"]}
    latest["sources"] = [a for a in sources if a["article_id"] in wanted]
    reader.import_result(latest)
    return reader, db, events, sources


def update_event(db, event):
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE events SET data=? WHERE event_id=?", (json.dumps(event), event["event_id"]))


def test_cumulative_selection_keeps_older_events_and_full_original_evidence(active_pool):
    reader, db, events, sources = active_pool
    original_db = db.read_bytes()
    assert len(reader.latest()[0]["events"]) == 4
    pool, _ = reader.cumulative()
    V8Result.model_validate(pool)
    assert len(pool["events"]) == 10
    chosen = reader.scenario_events()
    assert chosen["complete"] and chosen["gaps"] == []
    assert len(chosen["events"]) == 6
    for category in ("regulatory", "macroeconomics", "industry"):
        rows = [e for e in chosen["events"] if e["category"] == category]
        assert [e["event_id"] for e in rows] == [f"E{category}2", f"E{category}1"]
        assert chosen["available_counts"][category] == 3
    assert chosen["excluded_unscored"]["industry"] == 1
    selected = next(e for e in chosen["events"] if e["event_id"] == "Eregulatory2")
    assert len(selected["sources"]) == 2
    detail = reader.article(selected["primary_article_id"])
    expected = {s["article_id"]:s["text"] for s in sources}
    assert {s["article_id"]:s["text"] for s in detail["sources"]} == {aid:expected[aid] for aid in selected["article_ids"]}
    assert len(reader.events()["events"]) == 10
    assert reader.tracker()["total"] == 10
    assert db.read_bytes() == original_db
    assert reader.latest()[0]["run_id"] == "Rlatest" and len(reader.latest()[0]["events"]) == 4
    assert reader.cumulative()[0]["run_id"] == pool["run_id"]


def test_latest_event_state_replaces_prior_score_without_revival(active_pool):
    reader, db, events, _ = active_pool
    before, _ = reader.cumulative()
    revised = copy.deepcopy(next(e for e in events if e["event_id"] == "Eindustry2"))
    revised.update(score=None, why_it_matters=[], what_to_do=[])
    update_event(db, revised)
    after, _ = reader.cumulative()
    assert after["run_id"] != before["run_id"]
    rows = [e for e in reader.scenario_events()["events"] if e["category"] == "industry"]
    assert [e["event_id"] for e in rows] == ["Eindustry1", "Eindustry0"]
    old = reader.saved_run(before["run_id"])
    assert next(e for e in old["events"] if e["event_id"] == "Eindustry2")["score"]["total"] == 60
    assert next(e for e in after["events"] if e["event_id"] == "Eindustry2")["score"] is None


def test_forecast_older_pool_event_uses_immutable_snapshot_for_recommendations(active_pool):
    reader, db, events, _ = active_pool
    class Offline:
        config = None
        def _chat(self, messages):
            raise AssertionError("No model call is allowed in this test")
    store = IntelligenceStore(":memory:")
    forecast = ForecastAgent(store, reader, Offline()).predict(ForecastRequest(
        event_ids=["Eregulatory2"], as_of=date(2026,11,2), mode="rule_based"))
    original = copy.deepcopy(next(e for e in events if e["event_id"] == "Eregulatory2"))
    assert forecast["selected_event_ids"] == ["Eregulatory2"]
    snapshot = reader.saved_run(forecast["source_run_id"])
    assert snapshot and snapshot["metrics"]["kind"] == "cumulative_event_pool"
    assert set(forecast["evidence_links"]) == {s["url"] for s in snapshot["sources"] if s["article_id"] in original["article_ids"]}
    revised = copy.deepcopy(original)
    revised["what_to_do"] = ["Changed advice from a later run."]
    update_event(db, revised)
    newer, _ = reader.cumulative()
    assert newer["run_id"] != forecast["source_run_id"]
    saved = reader.saved_run(forecast["source_run_id"])
    assert next(e for e in saved["events"] if e["event_id"] == "Eregulatory2")["what_to_do"] == original["what_to_do"]
    finance = {"horizon_months":6,"all_actions":[],"cash_impact":{"net_eur_m":-12.5},
               "liquidity":{"scenario_headroom_eur_m":800,"scenario_min_date":"2027-02-28"},
               "calculation":{"run_id":"cash_test","status":"HIGH"}}
    actions = event_actions(finance,saved["events"],forecast["selected_event_ids"])
    assert actions[0]["text"] == original["what_to_do"][0]
    assert actions[0]["event_ids"] == ["Eregulatory2"]
    assert actions[0]["article_ids"] == original["article_ids"]


def test_active_database_does_not_union_events_from_previous_namespace(active_pool):
    reader, _, _, _ = active_pool
    old = example()
    old["run_id"] = "Rprevious_namespace"
    reader.import_result(old)
    pool, _ = reader.cumulative()
    assert "E05a5cc9d32647c0990a5" not in {e["event_id"] for e in pool["events"]}
    assert len(pool["events"]) == 10


@pytest.mark.parametrize("state", ["unset", "missing", "empty"])
def test_saved_export_remains_available_before_active_pool_exists(active_pool, monkeypatch, tmp_path, state):
    reader, db, _, _ = active_pool
    if state == "unset":
        monkeypatch.delenv("NEWS_V8_DATABASE")
    elif state == "missing":
        monkeypatch.setenv("NEWS_V8_DATABASE", str(tmp_path / "not-created.sqlite"))
    else:
        with sqlite3.connect(db) as connection:
            connection.execute("DELETE FROM events")
    pooled, source = reader.cumulative()
    latest, latest_source = reader.latest()
    assert pooled == latest and source == latest_source
    assert len(pooled["events"]) == 4
