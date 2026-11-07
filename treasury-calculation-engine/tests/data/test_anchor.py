"""Anchor averages and lock rates written into a new profile version (3.7, D70, P31)."""

import shutil
from datetime import date

import pytest
import yaml

from hlag_engine.company_profile.schema import DEFAULT_PROFILE_PATH, ACTIVE_SETTING_PATH, EntryStatus, load_profile, load_raw
from hlag_engine.data.anchor import (
    LOCK_RATE_DATE,
    AnchorCoverageError,
    AnchorError,
    compute_anchor_values,
    pending_targets,
    write_anchor_profile_version,
)
from tests.data.conftest import daily, market_row

H1_START, H1_END = date(2026, 1, 1), date(2026, 6, 30)
DAILY = ["SOFR", "ESTR", "BB_OAS", "UST_2Y", "EA_2Y", "FX_USD_PER_EUR", "FX_USD_PER_CAD", "FX_USD_PER_GBP",
         "FX_USD_PER_CNY", "FX_USD_PER_INR", "FX_USD_PER_BRL"]
WEEKLY = ["WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE", "VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM"]
BASE = {sid: 1.0 + i for i, sid in enumerate(DAILY + WEEKLY)}


def fill(store, skip=()):
    """Daily prints on weekdays (value alternates base, base + 1); weekly prints every 7 days."""
    rows = []
    for sid in DAILY:
        if sid in skip:
            continue
        days = [d for d in daily(H1_START, H1_END) if d.weekday() < 5]
        rows += [market_row(sid, d, BASE[sid] + (i % 2)) for i, d in enumerate(days)]
    for sid in WEEKLY:
        if sid not in skip:
            rows += [market_row(sid, d, BASE[sid] * 100) for d in daily(H1_START, H1_END, step=7)]
    for sid, v in (("FX_USD_PER_INR", 0.0111), ("FX_USD_PER_BRL", 0.1826)):
        if sid not in skip:
            rows.append(market_row(sid, LOCK_RATE_DATE, v))
    store.upsert_market_rows(rows)


def expected_daily(sid):
    days = [d for d in daily(H1_START, H1_END) if d.weekday() < 5]
    values = [BASE[sid] + (i % 2) for i in range(len(days))]
    return sum(values) / len(values)


@pytest.fixture
def real():
    return load_profile(DEFAULT_PROFILE_PATH)


def test_pending_targets_are_the_twenty_pending_history_items(real):
    targets = pending_targets(real)
    assert len(targets) == 20
    assert targets["fx.lock_rate_usd_per_unit.INR"] == "lock_rate" and targets["WCI_SHA_RTM"] == "average"
    assert "FX_USD_PER_EUR" not in targets  # DISCLOSED, to_verify


def test_compute_averages_real_prints_only(store, real):
    fill(store)
    # carry-forward rows must not enter the average
    store.upsert_market_rows([market_row("SOFR", date(2026, 1, 3), 999.0, filled=True, days=1)])
    result = compute_anchor_values(store, real)
    assert all(v.covered for v in result.values.values()), {k: v.problems for k, v in result.values.items()}
    assert result.values["SOFR"].value == pytest.approx(expected_daily("SOFR"))
    assert result.values["WCI_SHA_RTM"].value == BASE["WCI_SHA_RTM"] * 100
    assert result.values["fx.lock_rate_usd_per_unit.INR"].value == 0.0111
    assert result.eur_check["profile_value"] == 1.1667
    assert result.eur_check["ecb_average"] == pytest.approx(expected_daily("FX_USD_PER_EUR"))


def test_compute_writes_nothing(store, real):
    fill(store)
    compute_anchor_values(store, real)
    assert store.list_audit() == []


@pytest.mark.parametrize(
    ("mutate", "target", "problem"),
    [
        (lambda s: None, "SOFR", "no loaded history"),
        (lambda s: s.conn.execute("DELETE FROM market_data WHERE series_id='ESTR' AND date < '2026-01-20'"), "ESTR", "history starts"),
        (lambda s: s.conn.execute("DELETE FROM market_data WHERE series_id='ESTR' AND date > '2026-06-20'"), "ESTR", "history ends"),
        (lambda s: s.conn.execute("DELETE FROM market_data WHERE series_id='WCI_SHA_LAX' AND date BETWEEN '2026-03-01' AND '2026-03-10'"), "WCI_SHA_LAX", "gap of"),
        (lambda s: s.conn.execute("UPDATE market_data SET confirmed=0 WHERE series_id='VLSFO_SIN' AND date='2026-02-05'"), "VLSFO_SIN", "unconfirmed"),
        (lambda s: s.conn.execute("UPDATE market_data SET filled=1, days_since_print=1 WHERE series_id='FX_USD_PER_BRL' AND date='2025-12-31'"), "fx.lock_rate_usd_per_unit.BRL", "no published"),
    ],
)
def test_coverage_problems(store, real, mutate, target, problem):
    fill(store, skip=("SOFR",))
    mutate(store)
    value = compute_anchor_values(store, real).values[target]
    assert not value.covered and any(problem in p for p in value.problems), value.problems


def test_write_new_version(store, tmp_path, real):
    fill(store)
    source_bytes = DEFAULT_PROFILE_PATH.read_bytes()
    active_before = ACTIVE_SETTING_PATH.read_bytes()
    out = tmp_path / "company_profile_2026H1-v2.yaml"
    result = write_anchor_profile_version(store, out_path=out, new_version="2026H1-v2", actor="admin",
                                          source_path=DEFAULT_PROFILE_PATH)
    new = load_profile(out)
    assert new.profile_version == "2026H1-v2" and new.content_hash == result.content_hash != real.content_hash
    assert pending_targets(new) == {} and result.left_pending == []
    sofr = new.anchor_snapshot.series["SOFR"]
    assert sofr.value == pytest.approx(expected_daily("SOFR")) and sofr.status is EntryStatus.OK
    assert sofr.label.value == "DERIVED"
    lock = new.parameters["fx.lock_rate_usd_per_unit.INR"]
    assert (lock.value, lock.label.value, lock.status, lock.as_of) == (0.0111, "MARKET", EntryStatus.OK, LOCK_RATE_DATE)
    eur = new.anchor_snapshot.series["FX_USD_PER_EUR"]
    assert eur.value == 1.1667 and eur.status is EntryStatus.TO_VERIFY  # untouched
    # nothing else changed, comments kept, source and active setting untouched
    old_raw, new_raw = load_raw(DEFAULT_PROFILE_PATH), load_raw(out)
    for raw in (old_raw, new_raw):
        raw.pop("profile_version")
        for sid in result.values:
            raw["anchor_snapshot"]["series"].pop(sid, None)
            raw["parameters"].pop(sid, None)
    assert old_raw == new_raw
    assert out.read_text().splitlines()[0] == DEFAULT_PROFILE_PATH.read_text().splitlines()[0]
    assert DEFAULT_PROFILE_PATH.read_bytes() == source_bytes
    assert ACTIVE_SETTING_PATH.read_bytes() == active_before
    [entry] = store.list_audit(action="profile_version_written")
    assert entry.object_id == "2026H1-v2" and entry.actor == "admin"


def test_write_refuses_uncovered(store, tmp_path):
    fill(store, skip=("HSFO_RTM",))
    out = tmp_path / "v2.yaml"
    with pytest.raises(AnchorCoverageError) as exc:
        write_anchor_profile_version(store, out_path=out, new_version="v2", actor="admin", source_path=DEFAULT_PROFILE_PATH)
    assert list(exc.value.problems) == ["HSFO_RTM"]
    assert not out.exists() and store.list_audit() == []


def test_write_explicit_subset_leaves_others_pending(store, tmp_path):
    fill(store, skip=("HSFO_RTM",))
    out = tmp_path / "v2.yaml"
    result = write_anchor_profile_version(store, out_path=out, new_version="v2", actor="admin",
                                          source_path=DEFAULT_PROFILE_PATH, targets=["SOFR", "fx.lock_rate_usd_per_unit.BRL"])
    assert result.left_pending and "HSFO_RTM" in result.left_pending
    new = load_profile(out)
    assert new.anchor_snapshot.series["SOFR"].status is EntryStatus.OK
    assert new.anchor_snapshot.series["ESTR"].status is EntryStatus.PENDING_HISTORY
    assert new.parameters["fx.lock_rate_usd_per_unit.INR"].status is EntryStatus.PENDING_HISTORY


def test_write_guards(store, tmp_path):
    fill(store)
    existing = tmp_path / "exists.yaml"
    existing.write_text("x")
    kwargs = {"actor": "admin", "source_path": DEFAULT_PROFILE_PATH}
    with pytest.raises(AnchorError, match="never overwritten"):
        write_anchor_profile_version(store, out_path=existing, new_version="v2", **kwargs)
    with pytest.raises(AnchorError, match="differ"):
        write_anchor_profile_version(store, out_path=tmp_path / "a.yaml", new_version="2026H1-v1", **kwargs)
    with pytest.raises(AnchorError, match="not pending_history"):
        write_anchor_profile_version(store, out_path=tmp_path / "b.yaml", new_version="v2", targets=["FX_USD_PER_EUR"], **kwargs)
    copy = tmp_path / "copy.yaml"
    shutil.copy(DEFAULT_PROFILE_PATH, copy)
    with pytest.raises(AnchorError, match="never overwritten"):  # the source itself
        write_anchor_profile_version(store, out_path=copy, new_version="v2", actor="admin", source_path=copy)


def test_written_version_has_nothing_left_to_fill(store, tmp_path):
    fill(store)
    first = tmp_path / "v2.yaml"
    write_anchor_profile_version(store, out_path=first, new_version="v2", actor="admin", source_path=DEFAULT_PROFILE_PATH)
    with pytest.raises(AnchorError, match="nothing to fill"):
        write_anchor_profile_version(store, out_path=tmp_path / "v3.yaml", new_version="v3", actor="admin", source_path=first)
    assert yaml.safe_load(first.read_text())["profile_version"] == "v2"
