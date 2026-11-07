from datetime import date

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import FieldCategory, HedgeTest, ScenarioParams
from hlag_engine.contracts.enums import OwnerSpec, RefinancingMode, Shape, Trade

T0 = date(2026, 10, 31)


def params(**overrides):
    data = {"name": "test", "start_date": T0, "source": "custom"}
    data.update(overrides)
    return ScenarioParams(**data)


def hedge(**overrides):
    data = {
        "module": "bunker",
        "instrument": "swap",
        "target": "fuel_all",
        "coverage_pct": 50,
        "tenor_months": 12,
        "start_date": T0,
    }
    data.update(overrides)
    return HedgeTest(**data)


def fully_shocked():
    """A scenario with every shock and state field set to a non-default value."""
    return params(
        name="Everything moves",
        source="auto_built",
        preset_id="red_sea_reescalation",
        event_ids=["evt_1"],
        duration_days=182,
        default_shape="ramp",
        ramp_or_revert_weeks=8,
        freight={
            "rate_shock_pct": {"ASIA_EUROPE": 40},
            "volume_shock_pct": {"ASIA_EUROPE": -5},
            "transit_delay_days": {"ASIA_EUROPE": 12},
            "shape": "step",
            "history_replay": {"start": date(2024, 1, 1), "end": date(2024, 6, 30)},
            "rate_shock_path": {"ASIA_AMERICA": [{"date": date(2026, 11, 6), "pct": 3.5}]},
        },
        bunker={
            "vlsfo_shock_pct": 15,
            "hsfo_shock_pct": 12,
            "consumption_uplift_pct": 30,
            "uplift_affected_share": 0.35,
            "shape": "spike_revert",
        },
        rates={
            "rate_path": {"USD": [{"date": date(2026, 12, 10), "step_bp": -25}]},
            "credit_spread_change_bp": 150,
            "refinancing_mode": "unavailable",
            "zim_tranche_on": True,
        },
        fx={"move_pct": {"EUR": 10}, "shape": {"EUR": "step"}},
        direct={"cash_flows": [{"effect_id": "fine", "overlap_key": "fine", "date": date(2026, 12, 15),
                                "amount_usd_m": -50, "label": "Fine", "rationale": "Stated fine"}]},
        hedge_tests=[hedge()],
        overrides={"bunker.mfr_share": 0.5},
        unapproved_adjustment_ids=["adj_7"],
    )


# --- field categories -------------------------------------------------------


def test_every_field_is_tagged_shock_state_or_meta():
    """Guard: a new field without a category fails here (spec 2.7, D28)."""
    categories = ScenarioParams.field_categories()
    untagged = [path for path, cat in categories.items() if cat is None]
    assert untagged == []
    assert set(categories.values()) <= {FieldCategory.SHOCK, FieldCategory.STATE, FieldCategory.META}


def test_classification_matches_spec():
    cats = ScenarioParams.field_categories()
    shocks = {p for p, c in cats.items() if c is FieldCategory.SHOCK}
    states = {p for p, c in cats.items() if c is FieldCategory.STATE}
    metas = {p for p, c in cats.items() if c is FieldCategory.META}
    assert shocks == {
        "freight.rate_shock_pct", "freight.volume_shock_pct", "freight.transit_delay_days",
        "freight.history_replay", "freight.rate_shock_path",
        "bunker.vlsfo_shock_pct", "bunker.hsfo_shock_pct", "bunker.consumption_uplift_pct",
        "bunker.uplift_affected_share",
        "rates.rate_path", "rates.credit_spread_change_bp", "rates.refinancing_mode",
        "fx.move_pct",
        "direct.cash_flows",  # deviation from spec 2.7 (decisions_pending.md, "Direct cash flows")
    }  # fmt: skip
    assert states == {"rates.zim_tranche_on", "hedge_tests", "overrides", "unapproved_adjustment_ids"}
    assert metas == {
        "name", "start_date", "duration_days", "default_shape", "ramp_or_revert_weeks",
        "source", "event_ids", "preset_id", "freight.shape", "bunker.shape", "fx.shape",
    }  # fmt: skip


# --- zero_shock ------------------------------------------------------------------


def test_zero_shock_resets_every_shock_field():
    z = fully_shocked().zero_shock()
    assert z.freight.rate_shock_pct == {}
    assert z.freight.volume_shock_pct == {}
    assert z.freight.transit_delay_days == {}
    assert z.freight.history_replay is None
    assert z.freight.rate_shock_path == {}
    assert z.bunker.vlsfo_shock_pct == 0
    assert z.bunker.hsfo_shock_pct == 0
    assert z.bunker.consumption_uplift_pct == 0
    assert z.bunker.uplift_affected_share == 0
    assert z.rates.rate_path == {}
    assert z.rates.credit_spread_change_bp == 0
    assert z.rates.refinancing_mode is RefinancingMode.ROLLOVER
    assert z.fx.move_pct == {}


def test_zero_shock_keeps_state_and_meta_fields():
    original = fully_shocked()
    z = original.zero_shock()
    assert z.rates.zim_tranche_on is True
    assert z.hedge_tests == original.hedge_tests
    assert z.overrides == {"bunker.mfr_share": 0.5}
    assert z.unapproved_adjustment_ids == ["adj_7"]
    for name in ("name", "start_date", "duration_days", "default_shape", "ramp_or_revert_weeks",
                 "source", "event_ids", "preset_id"):  # fmt: skip
        assert getattr(z, name) == getattr(original, name)
    assert z.freight.shape is Shape.STEP
    assert z.bunker.shape is Shape.SPIKE_REVERT
    assert z.fx.shape == original.fx.shape


def test_zero_shock_checked_generically_against_tags():
    original = fully_shocked()
    z = original.zero_shock()
    defaults = params()
    for path, cat in ScenarioParams.field_categories().items():
        got, before, default = (_get(m, path) for m in (z, original, defaults))
        if cat is FieldCategory.SHOCK:
            assert got == default, path
            assert before != default, f"fixture should shock {path}"
        else:
            assert got == before, path


def test_zero_shock_is_idempotent_and_leaves_original_untouched():
    original = fully_shocked()
    snapshot = original.model_dump()
    z = original.zero_shock()
    assert z.zero_shock() == z
    assert original.model_dump() == snapshot
    assert z is not original


def test_zero_shock_of_baseline_is_unchanged():
    base = params()
    assert base.zero_shock() == base


def _get(model, path):
    for part in path.split("."):
        model = getattr(model, part)
    return model


# --- validation rules ------------------------------------------------------------


def test_minimal_scenario_defaults():
    p = params()
    assert p.default_shape is Shape.STEP
    assert p.duration_days is None
    assert p.rates.refinancing_mode is RefinancingMode.ROLLOVER
    assert p.drivers_set() == []


@pytest.mark.parametrize("source", ["preset", "custom", "scheduled"])
def test_unapproved_adjustments_only_when_auto_built(source):
    with pytest.raises(ValidationError, match="auto_built"):
        params(source=source, unapproved_adjustment_ids=["adj_1"])
    params(source="auto_built", unapproved_adjustment_ids=["adj_1"])


def test_preset_id_allowed_with_custom_source():
    p = params(source="custom", preset_id="oil_spike")
    assert p.preset_id.value == "oil_spike"


def test_unknown_preset_id_rejected():
    with pytest.raises(ValidationError):
        params(preset_id="red_sea")


def test_module_scoped_preset_cannot_set_other_driver():
    with pytest.raises(ValidationError, match="scoped to rates but sets freight"):
        params(
            source="preset",
            preset_id="credit_squeeze",
            rates={"credit_spread_change_bp": 200, "refinancing_mode": "unavailable"},
            freight={"rate_shock_pct": {"ASIA_EUROPE": -10}},
        )
    params(
        source="preset",
        preset_id="credit_squeeze",
        rates={"credit_spread_change_bp": 200, "refinancing_mode": "unavailable"},
    )


def test_scheduled_run_of_scoped_preset_is_checked():
    with pytest.raises(ValidationError, match="scoped to rates"):
        params(
            source="scheduled",
            preset_id="rates_up_100bp",
            rates={"rate_path": {"USD": [{"date": T0, "step_bp": 100}]}},
            fx={"move_pct": {"EUR": 1}},
        )


@pytest.mark.parametrize("source", ["custom", "auto_built"])
def test_based_on_preset_may_set_other_drivers(source):
    """For custom and auto_built, preset_id is provenance only."""
    p = params(
        source=source,
        preset_id="credit_squeeze",
        rates={"credit_spread_change_bp": 200},
        freight={"rate_shock_pct": {"ASIA_EUROPE": -10}},
    )
    assert p.drivers_set() == [OwnerSpec.FREIGHT, OwnerSpec.RATES]


def test_sensitivity_preset_is_single_driver():
    params(source="preset", preset_id="fx_eur_up_10", fx={"move_pct": {"EUR": 10}})
    with pytest.raises(ValidationError, match="scoped to fx"):
        params(
            source="preset",
            preset_id="fx_eur_up_10",
            fx={"move_pct": {"EUR": 10}},
            bunker={"vlsfo_shock_pct": 5},
        )


def test_joint_preset_may_set_several_drivers():
    p = params(
        source="preset",
        preset_id="red_sea_reescalation",
        freight={"rate_shock_pct": {"ASIA_EUROPE": 40}},
        bunker={"vlsfo_shock_pct": 15},
    )
    assert p.drivers_set() == [OwnerSpec.FREIGHT, OwnerSpec.BUNKER]


@pytest.mark.parametrize(
    "block",
    [
        {"freight": {"rate_shock_pct": {"ASIA_EUROPE": -100}}},
        {"freight": {"volume_shock_pct": {"ASIA_EUROPE": -100.5}}},
        {"freight": {"transit_delay_days": {"ASIA_EUROPE": -1}}},
        {"freight": {"rate_shock_pct": {"TRANSPACIFIC": 10}}},
        {"freight": {"rate_shock_path": {"ASIA_EUROPE": [{"date": T0, "pct": -100}]}}},
        {"freight": {"history_replay": {"start": date(2024, 6, 1), "end": date(2024, 1, 1)}}},
        {"bunker": {"vlsfo_shock_pct": -100}},
        {"bunker": {"uplift_affected_share": 1.5}},
        {"bunker": {"uplift_affected_share": -0.1}},
        {"bunker": {"consumption_uplift_pct": -101}},
        {"rates": {"rate_path": {"GBP": [{"date": T0, "step_bp": 25}]}}},
        {"rates": {"refinancing_mode": "default"}},
        {"fx": {"move_pct": {"USD": 5}}},
        {"fx": {"move_pct": {"JPY": 5}}},
        {"fx": {"move_pct": {"EUR": -100}}},
        {"default_shape": "wave"},
        {"source": "manual"},
        {"duration_days": 0},
        {"ramp_or_revert_weeks": -1},
        {"name": ""},
        {"unknown_field": 1},
    ],
)
def test_physical_limits_and_enums_rejected(block):
    with pytest.raises(ValidationError):
        params(**block)


def test_physical_limits_allow_edges():
    p = params(
        freight={"rate_shock_pct": {"ASIA_EUROPE": -99.9}, "volume_shock_pct": {"ASIA_EUROPE": -100}},
        bunker={"vlsfo_shock_pct": 500, "uplift_affected_share": 1},
        rates={"rate_path": {"EUR": [{"date": T0, "step_bp": -600}]}},
    )
    assert p.freight.rate_shock_pct[Trade.ASIA_EUROPE] == -99.9


def test_scenario_json_round_trip():
    p = fully_shocked()
    assert ScenarioParams.model_validate_json(p.model_dump_json()) == p


# --- hedge test -----------------------------------------------------------------


def test_hedge_test_defaults():
    assert hedge().strike_rule == "spot_at_T0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("module", "freight"),  # no freight hedge tool in v1 (6.7)
        ("instrument", "option"),
        ("coverage_pct", 101),
        ("coverage_pct", -1),
        ("tenor_months", 0),
        ("target", ""),
    ],
)
def test_hedge_test_rejects(field, value):
    with pytest.raises(ValidationError):
        hedge(**{field: value})
