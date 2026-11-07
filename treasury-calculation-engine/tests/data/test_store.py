"""SQLite store: the nine tables of spec 1.5 and the data-access layer."""

import sqlite3
from datetime import date

import pytest

from hlag_engine.contracts.enums import AdjustmentStatus
from hlag_engine.data.store import TABLES, Store, StoreError
from tests.contracts.test_adjustment import adjustment
from tests.contracts.test_lines import cash
from tests.contracts.test_records import NOW as RECORD_NOW
from tests.contracts.test_records import run_record
from tests.data.conftest import NOW, market_row
from hlag_engine.contracts import BaselineChange


def test_all_spec_tables_exist(store):
    assert set(TABLES) <= set(store.table_names())
    assert len(TABLES) == 10  # the nine tables of 1.5 plus alerts (P82)


def test_file_store_persists(tmp_path):
    path = tmp_path / "market.sqlite"
    s = Store(path, clock=lambda: NOW)
    s.upsert_market_rows([market_row("SOFR", date(2026, 10, 30), 3.9)])
    s.append_audit("tester", "x", "y")
    s.close()
    s = Store(path)
    assert s.get_market_row("SOFR", date(2026, 10, 30)).value == 3.9
    assert len(s.list_audit()) == 1
    s.close()


def test_audit_log_is_append_only(store):
    entry_id = store.append_audit("admin", "market_data_load", "obj", {"n": 1})
    entries = store.list_audit()
    assert [(e.actor, e.action, e.object_id, e.details) for e in entries] == [("admin", "market_data_load", "obj", {"n": 1})]
    assert entries[0].time == NOW
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("UPDATE audit_log SET actor = 'x' WHERE entry_id = ?", (entry_id,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("DELETE FROM audit_log")
    store.conn.rollback()
    assert len(store.list_audit()) == 1


def test_audit_filters(store):
    store.append_audit("a", "one", "x")
    store.append_audit("a", "two", "x")
    store.append_audit("a", "two", "y")
    assert len(store.list_audit(action="two")) == 2
    assert len(store.list_audit(action="two", object_id="y")) == 1


def test_market_rows_and_queries(store):
    store.upsert_market_rows([
        market_row("SOFR", date(2026, 10, 29), 3.9),
        market_row("SOFR", date(2026, 10, 30), 3.9, filled=True, days=1),
        market_row("SOFR", date(2026, 10, 31), 3.8),
    ])
    row = store.get_market_row("SOFR", date(2026, 10, 30))
    assert row.filled and row.days_since_print == 1 and row.label.value == "MARKET"
    assert store.latest_print_before("SOFR", date(2026, 10, 31)).date == date(2026, 10, 29)
    assert store.latest_on_or_before("SOFR", date(2026, 10, 30)).date == date(2026, 10, 30)
    assert [r.date.day for r in store.get_series("SOFR", include_filled=False)] == [29, 31]
    assert len(store.get_series("SOFR", start=date(2026, 10, 30))) == 2
    assert store.get_market_row("SOFR", date(2026, 1, 1)) is None


def test_market_confirm(store):
    store.upsert_market_rows([market_row("WCI_SHA_RTM", date(2026, 10, 29), 2000, confirmed=False)])
    store.set_market_confirmed("WCI_SHA_RTM", date(2026, 10, 29))
    assert store.get_market_row("WCI_SHA_RTM", date(2026, 10, 29)).confirmed
    with pytest.raises(StoreError):
        store.set_market_confirmed("WCI_SHA_RTM", date(2026, 1, 1))


def test_market_rejects_negative_days_since_print(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_market_rows([market_row("SOFR", date(2026, 10, 29), 3.9, days=-1)])


def test_run_records(store):
    store.save_run_record(run_record(run_id="run_1"))
    store.save_run_record(run_record(run_id="run_2"))
    assert store.get_run_record("run_1") == run_record(run_id="run_1")
    store.mark_run_superseded("run_1", "run_2")
    assert store.get_run_record("run_1").superseded_by == "run_2"
    with pytest.raises(StoreError):
        store.mark_run_superseded("run_x", "run_2")
    with pytest.raises(StoreError):
        store.mark_run_superseded("run_1", "run_x")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_run_record(run_record(run_id="run_1"))
    assert store.get_run_record("missing") is None


def test_cash_lines(store):
    lines = [cash(run_id="run_1"), cash(run_id="run_1", sub_id="freight:revenue:other")]
    store.save_cash_lines("run_1", lines)
    assert store.get_cash_lines("run_1") == lines
    assert store.get_cash_lines("run_2") == []
    with pytest.raises(StoreError):
        store.save_cash_lines("run_2", [cash(run_id="run_1")])


def test_adjustments(store):
    store.save_adjustment(adjustment())
    store.save_adjustment(adjustment(adjustment_id="adj_002"))
    approved = adjustment(status="approved", approver="treasurer", decided_at=RECORD_NOW)
    store.save_adjustment(approved)
    assert store.get_adjustment("adj_001").status is AdjustmentStatus.APPROVED
    assert [a.adjustment_id for a in store.list_adjustments(AdjustmentStatus.PROPOSED)] == ["adj_002"]
    assert len(store.list_adjustments()) == 2


def test_confirm_queue(store):
    qid = store.add_queue_item(series_id="WCI_SHA_RTM", on=date(2026, 10, 29), value=5000.0, unit="USD/FEU",
                               source_url="https://x", submitted_by="agent", failed_checks=["outside_plausibility_band"],
                               major_move_alert=True)
    item = store.get_queue_item(qid)
    assert item.state == "pending" and item.major_move_alert and item.failed_checks == ["outside_plausibility_band"]
    assert [i.queue_id for i in store.list_queue()] == [qid]
    store.resolve_queue_item(qid, state="confirmed", resolved_by="analyst", resolved_value=5000.0, note=None)
    assert store.get_queue_item(qid).state == "confirmed"
    assert store.list_queue() == [] and len(store.list_queue(state=None)) == 1
    with pytest.raises(StoreError):
        store.resolve_queue_item(qid, state="rejected", resolved_by="analyst", resolved_value=None, note=None)


def test_auto_build_queue(store):
    item = store.enqueue_auto_build("evt_1", 1, date(2026, 11, 3))
    with pytest.raises(sqlite3.IntegrityError):
        store.enqueue_auto_build("evt_1", 1, date(2026, 11, 3))
    assert store.list_auto_build(state="queued", queued_for=date(2026, 11, 3))[0].event_id == "evt_1"
    store.set_auto_build_state(item, "built", run_id="run_9")
    built = store.list_auto_build(state="built")[0]
    assert built.run_id == "run_9"
    with pytest.raises(StoreError):
        store.set_auto_build_state(999, "built")


def test_baseline_changes(store):
    change = BaselineChange(
        date=date(2026, 11, 2), previous_run_id="run_a", current_run_id="run_b", total_usd_m=-3.0,
        by_driver_usd_m={"freight": -1.0, "bunker": -1.0, "rates": 0.5, "fx": -0.5}, other_usd_m=-1.0,
    )
    store.save_baseline_change(change)
    assert store.list_baseline_changes() == [change]
    assert store.list_baseline_changes(start=date(2026, 11, 3)) == []


def test_transaction_rolls_back(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.upsert_market_rows([market_row("SOFR", date(2026, 10, 29), 3.9)])
            store.append_audit("a", "b", "c")
            raise RuntimeError("boom")
    assert store.get_market_row("SOFR", date(2026, 10, 29)) is None
    assert store.list_audit() == []


def test_tier_a_requests_dedupe_per_rule_per_day(store):
    first = store.enqueue_tier_a_request("freight.route_move.wci_sha_rtm", date(2026, 11, 2), {"move": 25.0})
    again = store.enqueue_tier_a_request("freight.route_move.wci_sha_rtm", date(2026, 11, 2), {"move": 30.0})
    next_day = store.enqueue_tier_a_request("freight.route_move.wci_sha_rtm", date(2026, 11, 3), {"move": 22.0})
    other_rule = store.enqueue_tier_a_request("bunker.hub_price_move.vlsfo_sin", date(2026, 11, 2), {"move": 16.0})
    assert first and again is None and next_day and other_rule
    signal = store.enqueue_auto_build("evt_1", 1, date(2026, 11, 2))  # signals keep event_id and version
    items = {i.item_id: i for i in store.list_auto_build()}
    assert items[signal].source == "signal" and items[signal].event_id == "evt_1" and items[signal].dedupe_key is None
    assert items[first].source == "tier_a" and items[first].event_id is None and items[first].payload == {"move": 25.0}
    assert len(store.list_auto_build(source="tier_a")) == 3


def test_old_files_are_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE auto_build_queue (item_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
            version INTEGER NOT NULL, queued_at TEXT NOT NULL, queued_for TEXT NOT NULL, state TEXT NOT NULL,
            run_id TEXT, UNIQUE (event_id, version));
        INSERT INTO auto_build_queue (event_id, version, queued_at, queued_for, state)
            VALUES ('evt_old', 2, '2026-11-01T00:00:00+00:00', '2026-11-01', 'queued');
        CREATE TABLE baseline_changes (change_id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
            previous_run_id TEXT NOT NULL, current_run_id TEXT NOT NULL, record_json TEXT NOT NULL,
            UNIQUE (date, current_run_id));
    """)
    conn.commit()
    conn.close()
    s = Store(path, clock=lambda: NOW)
    [item] = s.list_auto_build()
    assert (item.event_id, item.version, item.source) == ("evt_old", 2, "signal")
    assert "window" in s._columns("baseline_changes") and "alerts" in s.table_names()
    s.close()
