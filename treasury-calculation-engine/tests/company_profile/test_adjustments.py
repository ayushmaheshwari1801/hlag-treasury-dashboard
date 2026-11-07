"""validate_adjustment: catalogue rules, value modes, sizing warning (3.3, 3.7, D48, D52)."""

from datetime import date

import pytest

from hlag_engine.contracts import Adjustment, Signal
from hlag_engine.company_profile.adjustments import validate_adjustment
from hlag_engine.company_profile.resolve import RunContext
from tests.conftest import T0

BASE_SIGNAL = {
    "version": 1,
    "published_at": "2026-10-30T08:00:00+00:00",
    "headline": "h",
    "summary": "s",
    "evidence_links": ["https://example.com/a"],
    "direction": "down",
    "confidence": 0.8,
}


def signal(event_id, event_type, kind="qualitative", **extra):
    data = {**BASE_SIGNAL, "event_id": event_id, "event_type": event_type, "kind": kind, **extra}
    if kind == "quantitative":
        data.setdefault("magnitude", 5)
        data.setdefault("magnitude_unit", "pct")
    return Signal.model_validate(data)


SIGNALS = [
    signal("evt_reg", "regulation"),
    signal("evt_reg_q", "regulation", kind="quantitative"),
    signal("evt_fleet_fact", "fleet_change", kind="factual"),
    signal("evt_fin_fact", "financing_news", kind="factual"),
    signal("evt_fin_qual", "financing_news"),
    signal("evt_bank", "bank_failure", kind="factual"),
    signal("evt_weather", "other"),
    signal("evt_rating", "rating_change"),
]


def proposal(**overrides):
    data = {
        "adjustment_id": "adj_1",
        "param_key": "bunker.scrubber_share",
        "old_value": 0.15,
        "new_value": 0.05,
        "step": "large",
        "effective_date": date(2026, 11, 1),
        "review_date": date(2027, 4, 30),
        "event_ids": ["evt_reg"],
        "justification": "Regulation restricts HSFO with scrubbers; fleet register checked.",
        "confidence": 0.8,
        "proposed_by": "adjustment_agent",
    }
    data.update(overrides)
    return Adjustment(**data)


@pytest.fixture
def ctx(profile, registers):
    return RunContext(profile, T0, registers=registers)


def names(result):
    return {e.split(":")[0] for e in result.errors}


def warning_names(result):
    return {w.split(":")[0] for w in result.warnings}


def test_valid_step_proposal(ctx):
    r = validate_adjustment(proposal(), SIGNALS, ctx)
    assert r.ok and r.warnings == []


def test_step_in_either_direction(ctx):
    assert validate_adjustment(proposal(new_value=0.25), SIGNALS, ctx).ok  # +0.10 large


# --- step_or_direct ------------------------------------------------------------------------


def test_direct_value_from_quantitative_signal(ctx):
    r = validate_adjustment(proposal(step=None, new_value=0.08, event_ids=["evt_reg_q"]), SIGNALS, ctx)
    assert r.ok


def test_direct_value_from_qualitative_signal_rejected(ctx):
    r = validate_adjustment(proposal(step=None, new_value=0.08), SIGNALS, ctx)
    assert "direct_value_from_qualitative" in names(r)


def test_step_amount_must_match_catalogue(ctx):
    r = validate_adjustment(proposal(new_value=0.12, step="medium"), SIGNALS, ctx)  # medium = 0.05
    assert "step_amount_mismatch" in names(r)


# --- step_only (availability keys) --------------------------------------------------------


@pytest.mark.parametrize(("step", "new"), [("small", 0.75), ("medium", 0.5), ("large", 0.0)])
def test_availability_cuts_from_full(ctx, step, new):
    p = proposal(param_key="liquidity.T2.BANK_C", old_value=1.0, new_value=new, step=step, event_ids=["evt_bank"])
    assert validate_adjustment(p, SIGNALS, ctx).ok


def test_availability_cut_is_absolute_and_floored(ctx):
    adjusted = _approved("liquidity.T1.BANK_A", 0.5)
    c = RunContext(ctx.profile, T0, (adjusted,), registers=ctx.registers)
    def cut(step, new):
        return proposal(param_key="liquidity.T1.BANK_A", old_value=0.5, new_value=new, step=step,
                        event_ids=["evt_bank"], effective_date=date(2026, 12, 1))  # fmt: skip

    assert validate_adjustment(cut("medium", 0.0), SIGNALS, c).ok  # 0.5 - 0.5
    assert "step_amount_mismatch" in names(validate_adjustment(cut("medium", 0.25), SIGNALS, c))  # not proportional
    assert validate_adjustment(cut("small", 0.25), SIGNALS, c).ok  # 0.5 - 0.25
    assert validate_adjustment(cut("large", 0.0), SIGNALS, c).ok


def test_availability_direct_value_rejected(ctx):
    p = proposal(param_key="liquidity.T1.BANK_A", old_value=1.0, new_value=0.3, step=None, event_ids=["evt_bank"])
    assert "direct_value_not_allowed" in names(validate_adjustment(p, SIGNALS, ctx))


def test_unknown_bank_is_not_in_catalogue(ctx):
    p = proposal(param_key="liquidity.T1.BANK_Z", old_value=1.0, new_value=0.0, event_ids=["evt_bank"])
    assert "key_not_in_catalogue" in names(validate_adjustment(p, SIGNALS, ctx))


# --- switch ----------------------------------------------------------------------------------


def zim(**overrides):
    base = {"param_key": "rates.zim_tranche_on", "old_value": False, "new_value": True, "step": None,
            "event_ids": ["evt_fin_fact"]}  # fmt: skip
    return proposal(**{**base, **overrides})


def test_switch_from_factual_signal(ctx):
    r = validate_adjustment(zim(), SIGNALS, ctx)
    assert r.ok
    assert "future_dated_sizing_key" in warning_names(r)  # state keys resolve at T0 too


def test_switch_from_qualitative_rejected(ctx):
    assert "switch_needs_factual_or_quantitative" in names(validate_adjustment(zim(event_ids=["evt_fin_qual"]), SIGNALS, ctx))


def test_switch_rejects_step_and_non_bool(ctx):
    assert "step_not_allowed" in names(validate_adjustment(zim(step="small"), SIGNALS, ctx))
    assert "switch_value_not_bool" in names(validate_adjustment(zim(new_value=1), SIGNALS, ctx))


# --- catalogue rules -------------------------------------------------------------------------


def test_key_not_in_catalogue(ctx):
    p = proposal(param_key="liquidity.cash_and_equivalents_usd", old_value=3.15e9, new_value=3.0e9, step=None)
    assert names(validate_adjustment(p, SIGNALS, ctx)) == {"key_not_in_catalogue"}


def test_event_type_not_listed(ctx):
    assert "event_type_not_allowed" in names(validate_adjustment(proposal(event_ids=["evt_weather"]), SIGNALS, ctx))


def test_one_listed_event_is_enough(ctx):
    assert validate_adjustment(proposal(event_ids=["evt_weather", "evt_reg"]), SIGNALS, ctx).ok


def test_missing_event_ids(ctx):
    assert "missing_event_ids" in names(validate_adjustment(proposal(event_ids=[]), SIGNALS, ctx))


def test_unknown_event(ctx):
    assert "unknown_event" in names(validate_adjustment(proposal(event_ids=["evt_ghost"]), SIGNALS, ctx))


def test_hard_limit_breach(ctx):
    p = proposal(step=None, new_value=0.45, event_ids=["evt_reg_q"])
    assert "hard_limit_breach" in names(validate_adjustment(p, SIGNALS, ctx))


def test_review_window(ctx):
    ok = proposal(review_date=date(2027, 4, 30))  # 180 days after 1 Nov 2026
    assert validate_adjustment(ok, SIGNALS, ctx).ok
    too_long = proposal(review_date=date(2027, 5, 1))  # 181 days
    assert "review_too_long" in names(validate_adjustment(too_long, SIGNALS, ctx))


def test_old_value_must_match_resolved_value(ctx):
    assert "old_value_mismatch" in names(validate_adjustment(proposal(old_value=0.2, new_value=0.1), SIGNALS, ctx))


def test_only_proposals_are_validated(ctx):
    p = proposal(status="approved", approver="t", decided_at="2026-10-31T10:00:00+00:00")
    assert "not_a_proposal" in names(validate_adjustment(p, SIGNALS, ctx))


def test_signals_accepted_as_mapping(ctx):
    assert validate_adjustment(proposal(), {s.event_id: s for s in SIGNALS}, ctx).ok


# --- sizing warning (D52) --------------------------------------------------------------------


def test_future_dated_sizing_key_warns(ctx):
    p = proposal(param_key="rates.interest_bearing_cash_share", old_value=0.85, new_value=0.75, step="medium",
                 event_ids=["evt_bank"], effective_date=date(2027, 1, 1), review_date=date(2027, 6, 1))  # fmt: skip
    r = validate_adjustment(p, SIGNALS, ctx)
    assert r.ok and "future_dated_sizing_key" in warning_names(r)


def test_sizing_key_effective_today_does_not_warn(ctx):
    p = proposal(param_key="rates.interest_bearing_cash_share", old_value=0.85, new_value=0.75, step="medium",
                 event_ids=["evt_bank"], effective_date=T0, review_date=date(2027, 4, 1))  # fmt: skip
    assert validate_adjustment(p, SIGNALS, ctx).warnings == []


def test_future_dated_time_varying_key_does_not_warn(ctx):
    r = validate_adjustment(proposal(effective_date=date(2027, 3, 1), review_date=date(2027, 8, 1)), SIGNALS, ctx)
    assert r.ok and r.warnings == []


def test_placeholder_old_value_is_a_warning_not_an_error(real_profile, registers):
    """A to_enter base value cannot be checked yet; it does not block the proposal."""
    c = RunContext(real_profile, T0, registers=registers)
    assert validate_adjustment(proposal(), SIGNALS, c).ok


def _approved(key, value):
    return Adjustment(
        adjustment_id="prior", param_key=key, old_value=1.0, new_value=value, step="medium",
        effective_date=date(2026, 10, 1), review_date=date(2027, 3, 1), event_ids=["evt_bank"],
        justification="j", confidence=0.9, proposed_by="agent", status="approved",
        approver="treasurer", decided_at="2026-10-01T10:00:00+00:00",
    )  # fmt: skip
