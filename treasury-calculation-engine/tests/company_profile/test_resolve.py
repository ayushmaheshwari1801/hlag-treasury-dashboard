"""resolve(): three layers, time-dependent values (3.2, D52)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from hlag_engine.contracts import Adjustment, ScenarioParams
from hlag_engine.company_profile.resolve import (
    HardLimitBreach,
    OverrideNotAllowed,
    RunContext,
    active_adjustments,
    resolve,
    resolve_detail,
)
from hlag_engine.company_profile.schema import MissingProfileValue, UnknownParameter, profile_from_dict
from tests.conftest import T0, complete_raw

DECIDED = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def adj(key="bunker.scrubber_share", new=0.05, eff=date(2027, 3, 1), review=date(2027, 8, 1),
        status="approved", adj_id="adj_1", decided=DECIDED, old=0.15, step="large"):  # fmt: skip
    decided_fields = {"approver": "treasurer", "decided_at": decided} if status in ("approved", "rejected") else {}
    return Adjustment(
        adjustment_id=adj_id, param_key=key, old_value=old, new_value=new, step=step,
        effective_date=eff, review_date=review, event_ids=["evt_1"], justification="j",
        confidence=0.8, proposed_by="agent", status=status, **decided_fields,
    )  # fmt: skip


def ctx(profile, *adjustments, overrides=None, as_of=T0, unapproved=frozenset(), registers=None):
    return RunContext(profile, as_of, tuple(adjustments), overrides or {}, frozenset(unapproved), registers)


def test_profile_layer(profile):
    detail = resolve_detail("bunker.scrubber_share", T0, ctx(profile))
    assert (detail.value, detail.layer, detail.label.value) == (0.15, "profile", "ASSUMPTION")


def test_unknown_key(profile):
    with pytest.raises(UnknownParameter):
        resolve("bunker.nope", T0, ctx(profile))


def test_placeholder_raises_named_error(real_profile):
    with pytest.raises(MissingProfileValue) as info:
        resolve("debt.current_lease_liabilities_usd", T0, ctx(real_profile))
    assert info.value.key == "debt.current_lease_liabilities_usd" and info.value.status == "to_enter"


def test_time_varying_adjustment_applies_from_effective_to_review(profile):
    c = ctx(profile, adj())
    assert resolve("bunker.scrubber_share", date(2027, 2, 28), c) == 0.15
    assert resolve("bunker.scrubber_share", date(2027, 3, 1), c) == 0.05
    assert resolve("bunker.scrubber_share", date(2027, 7, 31), c) == 0.05
    assert resolve("bunker.scrubber_share", date(2027, 8, 1), c) == 0.15  # expired: drops out


def test_sizing_key_resolves_at_t0(profile):
    """D52: a sizing adjustment applies from the first run whose T0 is on/after its effective date."""
    a = adj(key="rates.interest_bearing_cash_share", new=0.75, old=0.85, step="medium",
            eff=date(2026, 12, 1), review=date(2027, 5, 1))  # fmt: skip
    before = ctx(profile, a, as_of=T0)
    assert resolve("rates.interest_bearing_cash_share", date(2027, 1, 15), before) == 0.85
    after = ctx(profile, a, as_of=date(2026, 12, 1))
    assert resolve("rates.interest_bearing_cash_share", date(2026, 12, 1), after) == 0.75
    assert resolve("rates.interest_bearing_cash_share", date(2027, 4, 1), after) == 0.75


def test_adjustments_apply_in_effective_date_order(profile):
    a1 = adj(adj_id="a1", new=0.10, eff=date(2027, 1, 1), review=date(2027, 6, 1))
    a2 = adj(adj_id="a2", new=0.05, eff=date(2027, 2, 1), review=date(2027, 6, 1))
    c = ctx(profile, a2, a1)
    assert resolve("bunker.scrubber_share", date(2027, 1, 15), c) == 0.10
    assert resolve("bunker.scrubber_share", date(2027, 3, 1), c) == 0.05


def test_same_effective_date_later_approval_wins(profile):
    early = adj(adj_id="early", new=0.10, decided=DECIDED)
    late = adj(adj_id="late", new=0.05, decided=DECIDED + timedelta(hours=1))
    for order in ((early, late), (late, early)):
        d = resolve_detail("bunker.scrubber_share", date(2027, 4, 1), ctx(profile, *order))
        assert (d.value, d.adjustment_id) == (0.05, "late")


@pytest.mark.parametrize("status", ["proposed", "rejected", "expired", "revoked", "withdrawn"])
def test_only_approved_adjustments_apply_in_normal_runs(profile, status):
    c = ctx(profile, adj(status=status))
    assert resolve("bunker.scrubber_share", date(2027, 4, 1), c) == 0.15


def test_unapproved_adjustment_applies_when_listed(profile):
    pending = adj(status="proposed", adj_id="pending_1")
    c = ctx(profile, pending, unapproved={"pending_1"})
    assert resolve("bunker.scrubber_share", date(2027, 4, 1), c) == 0.05


def test_for_scenario_lists_unapproved_only_for_auto_built(profile):
    pending = adj(status="proposed", adj_id="pending_1")
    auto = ScenarioParams(name="a", start_date=T0, source="auto_built", unapproved_adjustment_ids=["pending_1"])
    c = RunContext.for_scenario(profile, T0, auto, (pending,))
    assert resolve("bunker.scrubber_share", date(2027, 4, 1), c) == 0.05
    custom = ScenarioParams(name="c", start_date=T0, source="custom")
    c2 = RunContext.for_scenario(profile, T0, custom, (pending,))
    assert resolve("bunker.scrubber_share", date(2027, 4, 1), c2) == 0.15


def test_override_replaces_every_date(profile):
    c = ctx(profile, adj(), overrides={"bunker.scrubber_share": 0.25})
    for on in (T0, date(2027, 4, 1), date(2027, 10, 31)):
        d = resolve_detail("bunker.scrubber_share", on, c)
        assert (d.value, d.layer) == (0.25, "override")


def test_override_supplies_value_for_placeholder_free_key(profile):
    c = ctx(profile, overrides={"freight.contract_share": 0.6})
    assert resolve("freight.contract_share", T0, c) == 0.6


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("liquidity.cash_and_equivalents_usd", 1),  # disclosed: no allowed_range
        ("liquidity.interest_in_ocf", True),  # allowed_values only
        ("freight.contract_share", 0.9),  # outside [0.30, 0.70]
        ("fx.hedge_ratio.cad", 0.85),  # above the 80% cap
    ],
)
def test_override_not_allowed(profile, key, value):
    with pytest.raises(OverrideNotAllowed):
        resolve(key, T0, ctx(profile, overrides={key: value}))


def test_state_switch_can_be_overridden_per_run(profile):
    """P66: a state switch with allowed_values may be flipped for one run (run_pair with and without)."""
    assert resolve("fx.inr_brl_obligations_on", T0, ctx(profile)) is True
    assert resolve("fx.inr_brl_obligations_on", T0, ctx(profile, overrides={"fx.inr_brl_obligations_on": False})) is False
    for bad in (0, "false", None):
        with pytest.raises(OverrideNotAllowed):
            resolve("fx.inr_brl_obligations_on", T0, ctx(profile, overrides={"fx.inr_brl_obligations_on": bad}))


def test_adjustment_outside_hard_limits_fails(profile):
    bad = adj(new=0.55)  # scrubber hard limits [0, 0.40]
    with pytest.raises(HardLimitBreach):
        resolve("bunker.scrubber_share", date(2027, 4, 1), ctx(profile, bad))
    assert resolve("bunker.scrubber_share", date(2027, 1, 1), ctx(profile, bad)) == 0.15  # not yet active


def test_switch_adjustment(profile):
    on = adj(key="rates.zim_tranche_on", new=True, old=False, step=None, eff=date(2026, 9, 1), review=date(2027, 2, 1))
    assert resolve("rates.zim_tranche_on", T0, ctx(profile, on)) is True


def test_availability_keys(profile, registers):
    c = ctx(profile, registers=registers)
    assert resolve("liquidity.T1.BANK_A", T0, c) == 1.0
    cut = adj(key="liquidity.T2.BANK_C", new=0.0, old=1.0, eff=date(2026, 11, 15), review=date(2027, 5, 1))
    c2 = ctx(profile, cut, registers=registers)
    assert resolve("liquidity.T2.BANK_C", date(2026, 11, 14), c2) == 1.0
    assert resolve("liquidity.T2.BANK_C", date(2026, 11, 15), c2) == 0.0
    with pytest.raises(UnknownParameter):
        resolve("liquidity.T1.BANK_Z", T0, c)
    with pytest.raises(UnknownParameter):
        resolve("liquidity.T1.BANK_A", T0, ctx(profile))  # registers needed


def test_active_adjustments_lists_in_force(profile):
    a = adj()
    assert active_adjustments("bunker.scrubber_share", date(2027, 4, 1), ctx(profile, a)) == [a]
    assert active_adjustments("bunker.scrubber_share", date(2026, 12, 1), ctx(profile, a)) == []


def test_resolve_is_deterministic(profile):
    raw = complete_raw()
    p2 = profile_from_dict(raw)
    assert resolve("freight.gamma", T0, ctx(profile)) == resolve("freight.gamma", T0, ctx(p2))
