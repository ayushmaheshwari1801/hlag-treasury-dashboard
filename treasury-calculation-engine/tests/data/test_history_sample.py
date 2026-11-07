"""The synthetic Bloomberg-shaped weekly history (tests/fixtures/history): loads with terminal
references, warns on the 6 Mar 2026 fuel moves, and gives anchor averages over the WCI holiday gap
(25 of 26 H1 weeks) and all 26 fuel weeks (3.6, 3.7, P30, P31, P89)."""

import csv
from datetime import date
from pathlib import Path

import pytest

from hlag_engine.company_profile.schema import DEFAULT_PROFILE_PATH, load_profile
from hlag_engine.data.anchor import compute_anchor_values, round_sig
from hlag_engine.data.csv_history import load_history_csv

SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "history" / "weekly_history_sample.csv"
H1_START, H1_END = date(2026, 1, 1), date(2026, 6, 30)


def sample_rows():
    with open(SAMPLE, newline="") as handle:
        return [dict(r, date=date.fromisoformat(r["date"]), value=float(r["value"])) for r in csv.DictReader(handle)]


@pytest.fixture
def loaded(store):
    return store, load_history_csv(store, SAMPLE)


def test_sample_values_are_marked_synthetic():
    assert {r["notes"] for r in sample_rows()} == {"synthetic test value"}


def test_sample_loads_with_terminal_references(loaded):
    store, report = loaded
    assert report.rows_read == report.rows_written == 228
    assert report.by_series["WCI_SHA_RTM"] == 28 and report.by_series["VLSFO_SIN"] == 29
    assert store.get_series("WCI_COMPOSITE")[0].source_ref == "Bloomberg Terminal, WCIDCOMP Index (PX388 INTERVAL_AVG)"


def test_fuel_jumps_beyond_the_band_are_warnings_not_rejections(loaded):
    store, report = loaded
    warned = {(w.series_id, w.date, round(w.move_pct)) for w in report.warnings}
    assert {(s, d) for s, d, _ in warned} == {(s, date(2026, 3, 6)) for s in ("VLSFO_SIN", "HSFO_SIN", "HSFO_RTM")}
    assert len(store.list_audit(action="history_band_warning")) == 3
    assert store.get_market_row("VLSFO_RTM", date(2026, 3, 6)).confirmed  # +30%: inside the band


def test_anchor_averages_accept_the_wci_holiday_gap(loaded):
    store, _ = loaded
    values = compute_anchor_values(store, load_profile(DEFAULT_PROFILE_PATH)).values
    rows = sample_rows()
    for sid in ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE", "VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM"):
        h1 = [r["value"] for r in rows if r["series_id"] == sid and H1_START <= r["date"] <= H1_END]
        v = values[sid]
        assert v.covered, (sid, v.problems)
        assert v.prints == len(h1) == (25 if sid.startswith("WCI") else 26)
        assert v.value == round_sig(sum(h1) / len(h1))  # 10 significant digits
    assert values["WCI_SHA_RTM"].first_print == date(2026, 1, 9)  # 8 days after the start: within the 10-day limit
