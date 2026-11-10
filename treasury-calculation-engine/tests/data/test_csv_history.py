"""Hand-collected weekly history CSV loader (exact format, P30)."""

from datetime import date

import pytest

from hlag_engine import PACKAGE_DIR
from hlag_engine.data.csv_history import HEADER, HistoryCSVError, load_history_csv
from tests.data.conftest import market_row

HEAD = ",".join(HEADER)
URL = "https://example.com/wci"


def write(tmp_path, *rows, header=HEAD, name="history.csv"):
    path = tmp_path / name
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def errors_of(store, path, **kwargs):
    with pytest.raises(HistoryCSVError) as exc:
        load_history_csv(store, path, **kwargs)
    return exc.value.errors


def test_template_matches_format_and_loads_empty(store):
    template = PACKAGE_DIR / "data" / "history" / "weekly_history_template.csv"
    assert template.read_text().strip() == HEAD
    report = load_history_csv(store, template)
    assert report.rows_read == 0 and report.rows_written == 0


def test_valid_file_loads_as_market_confirmed(store, tmp_path):
    path = write(
        tmp_path,
        f"WCI_SHA_RTM,2026-01-08,2150.5,USD/FEU,{URL},first print",
        f"WCI_SHA_RTM,2026-01-15,2200,USD/FEU,{URL},",
        "VLSFO_SIN,2026-01-08,520,USD/t,https://example.com/ship,",
    )
    report = load_history_csv(store, path)
    assert report.rows_written == 3 and report.by_series == {"WCI_SHA_RTM": 2, "VLSFO_SIN": 1}
    assert report.last_print["WCI_SHA_RTM"] == date(2026, 1, 15) and report.warnings == []
    row = store.get_market_row("WCI_SHA_RTM", date(2026, 1, 8))
    assert (row.value, row.unit, row.label.value, row.confirmed, row.filled, row.source_ref) == (
        2150.5, "USD/FEU", "MARKET", True, False, URL
    )
    assert store.list_audit(action="history_csv_load")[0].details["rows_written"] == 3


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (f"WCI_XXX,2026-01-08,2000,USD/FEU,{URL},", "unknown_series"),
        (f"BRENT,2026-01-08,60,USD/bbl,{URL},", "unknown_series"),
        (f"WCI_SHA_RTM,08/01/2026,2000,USD/FEU,{URL},", "invalid_date"),
        (f"WCI_SHA_RTM,2026-02-30,2000,USD/FEU,{URL},", "invalid_date"),
        (f'WCI_SHA_RTM,2026-01-08,"2,000",USD/FEU,{URL},', "invalid_value"),
        (f"WCI_SHA_RTM,2026-01-08,,USD/FEU,{URL},", "missing_value"),
        (f"WCI_SHA_RTM,2026-01-08,0,USD/FEU,{URL},", "non_positive_value"),
        (f"WCI_SHA_RTM,2026-01-08,2000,USD/TEU,{URL},", "wrong_unit"),
        (f"VLSFO_SIN,2026-01-08,80,USD/bbl,{URL},", "wrong_unit"),
        ("WCI_SHA_RTM,2026-01-08,2000,USD/FEU,,", "missing_source_url"),
        (f"WCI_SHA_RTM,2026-01-08,2000,USD/FEU,{URL}", "wrong_column_count"),
    ],
)
def test_bad_row_rejects_whole_file(store, tmp_path, row, error):
    good = f"WCI_SHA_LAX,2026-01-08,3000,USD/FEU,{URL},"
    errors = errors_of(store, write(tmp_path, good, row))
    assert any(e.startswith(f"line 3: {error}") for e in errors), errors
    assert store.get_series("WCI_SHA_LAX") == []  # nothing written
    assert store.list_audit(action="history_csv_rejected")


def test_duplicate_row(store, tmp_path):
    row = f"WCI_SHA_RTM,2026-01-08,2000,USD/FEU,{URL},"
    assert any("duplicate_row" in e for e in errors_of(store, write(tmp_path, row, row)))


@pytest.mark.parametrize("header", ["series_id,date,value,unit,notes,source_url", "series,date,value,unit,source_url,notes", ""])
def test_header_must_be_exact(store, tmp_path, header):
    path = tmp_path / "h.csv"
    path.write_text(header + ("\n" if header else ""))
    assert len(errors_of(store, path)) == 1


def test_band_breach_is_warning_not_rejection(store, tmp_path):
    path = write(
        tmp_path,
        f"WCI_SHA_RTM,2026-01-08,2000,USD/FEU,{URL},",
        f"WCI_SHA_RTM,2026-01-15,3700,USD/FEU,{URL},",  # +85% > 80%
        f"WCI_SHA_RTM,2026-01-22,3700,USD/FEU,{URL},",
        f"HSFO_RTM,2026-01-08,400,USD/t,{URL},",
        f"HSFO_RTM,2026-01-15,550,USD/t,{URL},",  # +37.5% <= 40%
        f"HSFO_RTM,2026-01-22,800,USD/t,{URL},",  # +45% > 40%
    )
    report = load_history_csv(store, path)
    assert [(w.series_id, w.date) for w in report.warnings] == [
        ("HSFO_RTM", date(2026, 1, 22)), ("WCI_SHA_RTM", date(2026, 1, 15))
    ]
    assert report.warnings[1].move_pct == 85.0 and report.warnings[1].line == 3
    assert store.get_market_row("WCI_SHA_RTM", date(2026, 1, 15)).confirmed  # still loaded
    audit = store.list_audit(action="history_band_warning")
    assert {e.object_id for e in audit} == {"WCI_SHA_RTM:2026-01-15", "HSFO_RTM:2026-01-22"}


def test_band_uses_stored_previous_print(store, tmp_path):
    store.upsert_market_rows([market_row("VLSFO_RTM", date(2026, 1, 1), 500)])
    report = load_history_csv(store, write(tmp_path, f"VLSFO_RTM,2026-01-08,750,USD/t,{URL},"))
    assert len(report.warnings) == 1 and report.warnings[0].previous_value == 500


def test_reload_same_file_is_noop_and_conflict_needs_replace(store, tmp_path):
    path = write(tmp_path, f"WCI_SHA_RTM,2026-01-08,2000,USD/FEU,{URL},")
    load_history_csv(store, path)
    again = load_history_csv(store, path)
    assert again.rows_written == 0 and again.rows_unchanged == 1
    changed = write(tmp_path, f"WCI_SHA_RTM,2026-01-08,2100,USD/FEU,{URL},", name="changed.csv")
    assert any("conflicts_with_stored" in e for e in errors_of(store, changed))
    assert load_history_csv(store, changed, replace=True).rows_written == 1
    assert store.get_market_row("WCI_SHA_RTM", date(2026, 1, 8)).value == 2100


def test_same_value_confirms_unconfirmed_retrieved_value(store, tmp_path):
    store.upsert_market_rows([market_row("WCI_SHA_RTM", date(2026, 1, 8), 2000, confirmed=False)])
    load_history_csv(store, write(tmp_path, f"WCI_SHA_RTM,2026-01-08,2000,USD/FEU,{URL},"))
    assert store.get_market_row("WCI_SHA_RTM", date(2026, 1, 8)).confirmed


def test_stale_series_reported_with_as_of(store, tmp_path):
    path = write(tmp_path, f"WCI_SHA_RTM,2026-01-04,2000,USD/FEU,{URL},", f"VLSFO_SIN,2026-01-15,500,USD/t,{URL},")
    report = load_history_csv(store, path, as_of=date(2026, 1, 19))
    assert report.stale_series == ["WCI_SHA_RTM"]  # 15 days > 14; VLSFO_SIN 4 days


# ---------------------------------------------------------------- Bloomberg Terminal references (P89)

TERMINAL = "Bloomberg Terminal, WCIDSHRO Index (PX388 INTERVAL_AVG)"


@pytest.mark.parametrize("ref", [TERMINAL, "Bloomberg Terminal, LQM2RD38 Index (ClearLynx, PX_LAST)"])
def test_bloomberg_terminal_reference_is_a_valid_source(store, tmp_path, ref):
    load_history_csv(store, write(tmp_path, f'WCI_SHA_RTM,2026-01-09,2000,USD/FEU,"{ref}",'))
    [row] = store.get_series("WCI_SHA_RTM")
    assert row.source_ref == ref and row.confirmed and row.label.value == "MARKET"


@pytest.mark.parametrize("ref", [
    "Bloomberg Terminal", "Bloomberg Terminal, WCIDSHRO Index", "Bloomberg, WCIDSHRO Index (PX_LAST)",
    "WCIDSHRO Index (PX_LAST)", "Bloomberg Terminal, wcidshro Index (PX_LAST)", "ftp://example.com",
])
def test_other_source_references_are_rejected(store, tmp_path, ref):
    errors = errors_of(store, write(tmp_path, f'WCI_SHA_RTM,2026-01-09,2000,USD/FEU,"{ref}",'))
    assert any("missing_source_url" in e for e in errors), errors
