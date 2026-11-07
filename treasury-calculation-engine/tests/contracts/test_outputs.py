"""RunSummary, PairResult and MetricResult (spec 4.8)."""

import copy
import json
from datetime import date

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import MetricResult, PairResult, RunSummary
from hlag_engine.contracts.enums import ShortfallTier

# The joint Red Sea run summary example from spec 4.8, verbatim.
SPEC_EXAMPLE = json.loads("""
{
  "run_id": "run_20261031_joint_red_sea",
  "as_of": "2026-10-31",
  "scenario": {"name": "Red Sea re-escalation", "source": "preset", "preset_id": "red_sea_reescalation", "event_ids": []},
  "status": "LOW",
  "opportunity": false,
  "flags": {"synthetic_register": true, "proxy_based": false, "uses_unconfirmed_data": false, "includes_unapproved_adjustments": false, "incomplete_data": false},
  "headroom": {"baseline_min_usd_m": 500.0, "baseline_min_date": "2027-09-30", "scenario_min_usd_m": 462.441, "scenario_min_date": "2027-09-30", "consumed_pct": 7.51, "first_breach_date": null, "shortfall_tier": "within_operating_cash"},
  "attribution_usd_m": {"freight": 15.96, "bunker": -53.519, "rates": 0.0, "fx": 0.0, "total": -37.559},
  "desk_exceptions": [{"driver": "freight", "gross_adverse_usd_m": 168.0, "pct_of_headroom": 33.6, "date": "2026-12-31"}],
  "alert_ids": ["alert_desk_freight_001"]
}
""")


def summary(**changes):
    data = copy.deepcopy(SPEC_EXAMPLE)
    for path, value in changes.items():
        target = data
        *parents, leaf = path.split("__")
        for p in parents:
            target = target[p]
        target[leaf] = value
    return RunSummary.model_validate(data)


def test_spec_example_validates():
    s = RunSummary.model_validate(SPEC_EXAMPLE)
    assert s.headroom.shortfall_tier is ShortfallTier.WITHIN_OPERATING_CASH
    assert s.desk_exceptions[0].date == date(2026, 12, 31)
    # The 4.8 example validates as written; its dump adds the desk exception kind fields (P80).
    expected = copy.deepcopy(SPEC_EXAMPLE)
    expected["desk_exceptions"][0].update(kind="driver", series=[], detail=None)
    # Deviation from spec 4.3 (decisions_pending.md, "Direct cash flows"): the direct bar, 0 without direct flows.
    expected["attribution_usd_m"]["direct"] = 0.0
    assert json.loads(s.model_dump_json()) == expected


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("status", "MEDIUM"),
        ("scenario__source", "manual"),
        ("scenario__preset_id", "red_sea"),
        ("headroom__shortfall_tier", "overdraft"),
        ("flags__new_flag", True),
    ],
)
def test_summary_rejects(path, value):
    with pytest.raises(ValidationError):
        summary(**{path: value})


def test_desk_exception_driver_must_be_a_driver():
    data = copy.deepcopy(SPEC_EXAMPLE)
    data["desk_exceptions"][0]["driver"] = "liquidity"
    with pytest.raises(ValidationError):
        RunSummary.model_validate(data)


def test_opportunity_is_a_plain_engine_flag():
    """The -10% threshold is configuration, so the contract does not tie opportunity to c."""
    assert summary(opportunity=True).opportunity is True  # c = 7.51, still accepted
    assert summary(headroom__consumed_pct=-15.0, opportunity=False).opportunity is False
    s = summary(headroom__consumed_pct=None, status="CRITICAL")  # baseline breach: c is null
    assert s.headroom.consumed_pct is None
    with pytest.raises(ValidationError):
        summary(opportunity="sometimes")


def pair(**extra):
    run_b = copy.deepcopy(SPEC_EXAMPLE)
    run_b["run_id"] = "run_b"
    data = {
        "pair_id": "pair_001",
        "run_a": SPEC_EXAMPLE,
        "run_b": run_b,
        "difference": {
            "min_headroom_usd_m": 12.5,
            "attribution_usd_m": {"freight": 0, "bunker": 12.5, "rates": 0, "fx": 0, "total": 12.5},
            "line_totals_usd_m": {"HEDGE_SETTLEMENT": 12.5},
        },
        "label": "state comparison",
    }
    data.update(extra)
    return PairResult.model_validate(data)


def test_run_pair_result():
    p = pair()
    assert p.hedge_metrics is None and p.label == "state comparison"


def test_test_hedge_result_with_metrics():
    p = pair(
        hedge_metrics={
            "saving_adverse_usd_m": 20.1,
            "cost_favourable_usd_m": 18.4,
            "settlement_cash": [{"date": "2026-11-30", "usd_m": 1.2}],
            "margin_cash": [{"date": "2026-11-30", "usd_m": -3.0}],
            "margin_test": {"passed": True, "lowest_margin_eligible_balance_usd_m": 410.0},
        }
    )
    assert p.hedge_metrics.margin_test.passed is True


def test_pair_rejects_bad_label_and_line_code():
    with pytest.raises(ValidationError):
        pair(label="scenario delta")
    with pytest.raises(ValidationError):
        pair(difference={
            "min_headroom_usd_m": 0,
            "attribution_usd_m": {"freight": 0, "bunker": 0, "rates": 0, "fx": 0, "total": 0},
            "line_totals_usd_m": {"HEDGE": 1},
        })  # fmt: skip


def test_metric_result():
    m = MetricResult(name="consumed_pct", value=7.51, unit="%", label="ASSUMPTION",
                     formula="(H_b* - H_s*) / H_b*", run_id="run_1")  # fmt: skip
    assert m.label.value == "ASSUMPTION"
    with pytest.raises(ValidationError):
        MetricResult(name="x", value=1, unit="", label="FACT", formula="", run_id="run_1")


def test_desk_exception_kinds():
    from hlag_engine.contracts import DeskException

    legacy = DeskException(driver="freight", gross_adverse_usd_m=168.0, pct_of_headroom=33.6, date=date(2026, 12, 31))
    assert legacy.kind.value == "driver"  # the original four-field form still validates (P80)
    margin = DeskException(kind="margin", gross_adverse_usd_m=12.5, date=date(2027, 1, 31), detail="T4 below margin")
    data = DeskException(kind="data", series=["VLSFO_SIN"], detail="stale")
    assert margin.driver is None and data.series == ["VLSFO_SIN"]
    with pytest.raises(ValidationError):
        DeskException(kind="driver", gross_adverse_usd_m=1.0)
    with pytest.raises(ValidationError):
        DeskException(kind="data")
