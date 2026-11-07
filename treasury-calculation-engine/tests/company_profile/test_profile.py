"""Company profile schema and loader (3.1, 3.3, 3.7, D70)."""

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from hlag_engine.contracts.enums import EventType, ProvenanceLabel
from hlag_engine.company_profile.schema import (
    EntryStatus,
    ParamKind,
    ProfileEntry,
    UnknownParameter,
    profile_from_dict,
)
from tests.conftest import complete_raw

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_real_profile_loads(real_profile, v1_profile):
    assert real_profile.profile_version == "2026H1-v4"  # the active version (P93)
    assert v1_profile.profile_version == "2026H1-v1"
    assert real_profile.content_hash.startswith("sha256:")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("liquidity.minimum_operational_reserve_usd", 750_000_000),
        ("liquidity.interest_in_ocf", False),
        ("liquidity.count_liquid_investments", False),
        ("liquidity.margin_eligible_share_of_T1", 0.25),
        ("adjustments.max_review_days", 180),
        ("freight.contract_share", 0.50),
        ("freight.index_linked_share", 0.10),
        ("freight.beta", 1.0),
        ("freight.pricing_lag_days", 28),
        ("freight.kappa", 0.8),
        ("freight.clause_lag_days", 30),
        ("freight.gamma", 0.5),
        ("freight.leakage_gap_down", 0.30),
        ("freight.leakage_share_up", 0.10),
        ("freight.leakage_volume_loss", 0.0),
        ("freight.non_usd_share.ASIA_EUROPE", 0.05),
        ("freight.non_usd_share.ASIA_AMERICA", 0.03),
        ("freight.growth_g", 0.02),
        ("freight.collection_lag_days", 49),
        ("freight.delay_affected_share", 1.0),
        ("bunker.hub_weight_sin", 0.60),
        ("bunker.scrubber_share", 0.15),
        ("bunker.supplier_lag_days", 30),
        ("bunker.mfr_share", 0.70),
        ("bunker.mfr_lag_months", 2),
        ("bunker.asia_europe_consumption_share", 0.35),
        ("bunker.beta_fallback", 1.0),
        ("bunker.margin_factor", 1.0),
        ("rates.floating_split_usd", 0.70),
        ("rates.spread_usd_bp", 200),
        ("rates.spread_eur_bp", 225),
        ("rates.fixed_rate_pct", 4.8),
        ("rates.interest_bearing_cash_share", 0.85),
        ("rates.deposit_split_usd", 0.80),
        ("rates.deposit_spread_bp", 10),
        ("rates.new_issue_premium_bp", 0),
        ("rates.swap_spread_bp", 0),
        ("rates.refinancing_mode", "rollover"),
        ("rates.zim_tranche_on", False),
        ("rates.zim_amount_usd", 4_200_000_000),
        ("rates.zim_spread_bp", 225),
        ("fx.hedge_ratio.cad", 0.70),
        ("fx.hedge_ratio.eur_debt", 1.0),
        ("fx.hedge_ratio.inr_brl", 1.0),
        ("fx.eur_operating_cost_per_quarter_eur", 350_000_000),
        ("fx.inr_brl_obligations_on", True),
        ("fx.forward_points", 0),
    ],
)
def test_section_12_defaults(v1_profile, key, value):
    """Version 2026H1-v1 holds the section 12 defaults; later versions replace some with disclosures."""
    assert v1_profile.entry(key).value == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("liquidity.cash_and_equivalents_usd", 3_150_000_000),
        ("liquidity.special_fund_usd", 2_035_000_000),
        ("liquidity.unused_credit_lines_usd", 725_000_000),
        ("debt.current_financial_debt_and_leases_usd", 1_852_000_000),
        ("liquidity.operating_cash_flow_h1_usd", 849_000_000),
        ("freight.trade_receivables_usd", 2_998_000_000),
        ("freight.transport_volume_h1_teu", 6_684_000),
        ("bunker.consumption_h1_t", 2_400_000),
        ("rates.variable_rate_debt_eur", 1_999_100_000),
        ("fx.inr_obligation_inr", 1_400_000_000),
        ("fx.brl_obligation_brl", 256_000_000),
    ],
)
def test_disclosed_anchors(real_profile, key, value):
    entry = real_profile.entry(key)
    assert entry.value == value
    assert entry.label is ProvenanceLabel.DISCLOSED
    assert entry.source is not None and not entry.adjustable


def test_derived_figures(real_profile):
    assert real_profile.entry("liquidity.baseline_ocf_daily_usd").value == pytest.approx(849e6 / 181, abs=0.01)
    assert real_profile.entry("bunker.fuel_per_teu_t").value == pytest.approx(2.4e6 / 6.684e6, abs=1e-6)
    assert real_profile.entry("bunker.monthly_consumption_t").value == 400_000
    assert real_profile.entry("rates.financial_debt_total_eur").value == pytest.approx(1528.0e6 + 301.4e6 + 920.7e6)


def test_every_label_rule_holds(real_profile):
    for key, entry in real_profile.parameters.items():
        if entry.label is ProvenanceLabel.DISCLOSED:
            assert entry.source and not entry.adjustable, key
        if entry.label is ProvenanceLabel.DERIVED:
            assert entry.formula, key
        if entry.label is ProvenanceLabel.ASSUMPTION:
            assert entry.rationale and (entry.allowed_range or entry.allowed_values), key


def test_every_catalogue_event_type_is_in_the_2_8_enum(real_profile):
    entries = [*real_profile.catalogue.values(), real_profile.availability_catalogue.entry]
    for entry in entries:
        for event_type in entry.event_types:
            assert event_type in set(EventType)


def test_unknown_catalogue_event_type_rejected():
    raw = complete_raw()
    raw["catalogue"]["freight.gamma"]["event_types"] = ["contract_season", "weather"]
    with pytest.raises(ValidationError):
        profile_from_dict(raw)


def test_catalogue_matches_adjustable_entries(real_profile):
    adjustable = {k for k, e in real_profile.parameters.items() if e.adjustable}
    assert adjustable == set(real_profile.catalogue)
    assert len(real_profile.catalogue) == 24


def test_value_modes(real_profile):
    modes = {k: e.value_mode.value for k, e in real_profile.catalogue.items()}
    assert modes["rates.zim_tranche_on"] == "switch"
    assert modes["fx.inr_brl_obligations_on"] == "switch"
    assert real_profile.availability_catalogue.entry.value_mode.value == "step_only"
    assert all(m == "step_or_direct" for k, m in modes.items() if not k.endswith("_on"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"label": "DISCLOSED", "source": None}, "need source"),
        ({"label": "DISCLOSED", "source": {"report": "AR"}, "adjustable": True}, "adjustable: false"),
        ({"label": "DERIVED", "formula": None, "allowed_range": None}, "need formula"),
        ({"allowed_range": None, "allowed_values": None}, "allowed_range or allowed_values"),
        ({"rationale": None}, "rationale"),
        ({"value": 2.0}, "outside its allowed range"),
        ({"value": None}, "status is not to_enter"),
        ({"status": "to_verify", "note": None}, "need a note"),
        ({"status": "to_enter"}, "needs value: null"),
    ],
)
def test_entry_rules_rejected(change, message):
    base = {
        "value": 0.5, "label": "ASSUMPTION", "kind": "sizing", "allowed_range": [0, 1],
        "rationale": "r",
    }  # fmt: skip
    with pytest.raises(ValidationError, match=message):
        ProfileEntry(**{**base, **change})


def test_unknown_entry_field_rejected():
    with pytest.raises(ValidationError):
        ProfileEntry(value=1, label="MARKET", kind="sizing", colour="red")


def test_disclosed_key_cannot_be_in_catalogue():
    raw = complete_raw()
    raw["catalogue"]["liquidity.cash_and_equivalents_usd"] = copy.deepcopy(raw["catalogue"]["freight.gamma"])
    with pytest.raises(ValidationError, match="adjustable|disclosed"):
        profile_from_dict(raw)


def test_adjustable_key_must_be_in_catalogue():
    raw = complete_raw()
    del raw["catalogue"]["freight.gamma"]
    with pytest.raises(ValidationError, match="not in the catalogue"):
        profile_from_dict(raw)


def test_unknown_key_raises(profile):
    with pytest.raises(UnknownParameter):
        profile.entry("freight.no_such_key")


def test_content_hash_is_stable_and_changes_with_values():
    a, b = profile_from_dict(complete_raw()), profile_from_dict(complete_raw())
    assert a.content_hash == b.content_hash
    raw = complete_raw()
    raw["parameters"]["freight.gamma"]["value"] = 0.4
    assert profile_from_dict(raw).content_hash != a.content_hash


# --- anchor snapshot (3.7, D69, D70) -----------------------------------------------------


def test_anchor_snapshot_shape(real_profile):
    anchor = real_profile.anchor_snapshot
    assert anchor.period == "H1 2026"
    assert (anchor.start.isoformat(), anchor.end.isoformat()) == ("2026-01-01", "2026-06-30")
    usd_eur = anchor.series["FX_USD_PER_EUR"]
    assert usd_eur.value == 1.1667 and usd_eur.status is EntryStatus.OK  # verified in 2026H1-v2
    expected = {
        "WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE",
        "VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM",
        "SOFR", "ESTR", "BB_OAS", "UST_2Y", "EA_2Y",
        "FX_USD_PER_EUR", "FX_USD_PER_CAD", "FX_USD_PER_GBP", "FX_USD_PER_CNY",
        "FX_USD_PER_INR", "FX_USD_PER_BRL",
    }  # fmt: skip
    assert set(anchor.series) == expected
    assert not [k for k, s in anchor.series.items() if s.status is EntryStatus.PENDING_HISTORY]  # filled in v3
    assert all(s.value is not None for s in anchor.series.values())


def test_anchor_period_must_match_run_rate_and_p0():
    raw = complete_raw()
    raw["anchor_snapshot"]["period"] = "9M 2026"
    with pytest.raises(ValidationError, match="replace them together"):
        profile_from_dict(raw)


def test_newer_report_replaces_run_rate_p0_and_anchor_together():
    """14.3: a newer report replaces the OCF run-rate and the anchor snapshot in one version."""
    raw = complete_raw()
    raw["profile_version"] = "2026Q3-v1"
    raw["anchor_snapshot"]["period"] = "Q3 2026"
    raw["parameters"]["liquidity.baseline_ocf_daily_usd"]["period"] = "Q3 2026"
    with pytest.raises(ValidationError, match="freight.p0"):
        profile_from_dict(raw)  # P0 not yet replaced
    for key in raw["parameters"]:
        if key.startswith("freight.p0."):
            raw["parameters"][key]["period"] = "Q3 2026"
    assert profile_from_dict(raw).anchor_snapshot.period == "Q3 2026"


def test_param_kinds_present(real_profile):
    assert real_profile.entry("rates.interest_bearing_cash_share").kind is ParamKind.SIZING
    assert real_profile.entry("bunker.scrubber_share").kind is ParamKind.TIME_VARYING
    assert real_profile.entry("rates.zim_tranche_on").kind is ParamKind.STATE


# --- placeholders checklist ---------------------------------------------------------------


def test_every_placeholder_is_in_docs_placeholders(real_profile):
    text = (DOCS / "placeholders.md").read_text()
    for key in real_profile.placeholders():
        assert f"`{key}`" in text, key
    for series, s in real_profile.anchor_snapshot.series.items():
        if s.status is not EntryStatus.OK:
            assert f"`anchor_snapshot.{series}`" in text, series


def test_placeholders_have_notes(real_profile):
    for key, entry in real_profile.placeholders().items():
        assert entry.note, key


def test_profile_version_v2_filled():
    from datetime import date

    from hlag_engine.company_profile.schema import DEFAULT_PROFILE_PATH, PROFILE_DIR, load_profile

    v2 = load_profile(PROFILE_DIR / "company_profile_2026H1-v2.yaml")
    v1 = load_profile(DEFAULT_PROFILE_PATH)
    assert v2.profile_version == "2026H1-v2"
    p = v2.parameters
    assert p["rates.zim_amount_usd"].value == 2_500_000_000 and p["rates.zim_consideration_usd"].value == 4_200_000_000
    assert p["fx.usd_per_eur_close_2025_12_31"].value == 1.1756 and p["fx.usd_per_eur_close_2026_06_30"].value == 1.1396
    assert p["freight.p0.ASIA_EUROPE"].label.value == "DISCLOSED" and p["freight.p0.ASIA_EUROPE"].source.page == "10"
    assert p["freight.collection_lag_days"].value == 46.7 and p["freight.collection_lag_days"].label.value == "DERIVED"
    assert p["liquidity.annual_lease_repayments_usd"].value == 1_302_682_360
    assert p["fx.dividend_reference_eur"].value == 527_300_000
    assert p["rates.fomc_meeting_dates"].value[0] == date(2026, 12, 9)  # decision day = first day + 1
    assert p["rates.ecb_meeting_dates"].value[-1] == date(2027, 10, 28)
    assert p["rates.bond_coupon_month_day"].value == "04-15;10-15"
    assert p["rates.bond_maturity_date"].value == date(2028, 4, 15)
    assert p["rates.zim_draw_date"].value == date(2026, 12, 31) and p["rates.zim_draw_date"].status.value == "to_verify"
    assert p["rates.zim_spread_bp"].value == v1.parameters["rates.zim_spread_bp"].value
    assert v1.parameters["rates.zim_amount_usd"].value == 4_200_000_000  # v1 unchanged
    assert p["bunker.scrubber_share"].value == 0.15 and p["bunker.scrubber_share"].status.value == "to_verify"
    assert "Not Found" in p["bunker.scrubber_share"].note
    assert p["debt.current_lease_liabilities_usd"].value is None
    assert v2.anchor_snapshot.series["FX_USD_PER_EUR"].status.value == "ok"


def test_profile_version_v3_fills_the_pending_history(v2_profile):
    from hlag_engine.company_profile.schema import PROFILE_DIR, EntryStatus, load_profile

    v3 = load_profile(PROFILE_DIR / "company_profile_2026H1-v3.yaml")
    assert v3.profile_version == "2026H1-v3"
    for sid, s in v3.anchor_snapshot.series.items():
        assert s.status is EntryStatus.OK and s.value is not None, sid
        old = v2_profile.anchor_snapshot.series[sid]
        if old.status is EntryStatus.PENDING_HISTORY:
            assert s.label is old.label and s.formula == old.formula  # only value and status change
        else:
            assert s == old
    p = v3.parameters
    assert p["fx.lock_rate_usd_per_unit.INR"].value == 0.01112726274
    assert p["fx.lock_rate_usd_per_unit.BRL"].value == 0.1825554658
    assert v3.anchor_snapshot.series["WCI_SHA_RTM"].value == 2646.52
    changed = {k for k in p if p[k] != v2_profile.parameters[k]}
    assert changed == {"fx.lock_rate_usd_per_unit.INR", "fx.lock_rate_usd_per_unit.BRL"}


INTERIM_FREIGHT = {"freight.contract_share": 0.70, "freight.beta": 0.5, "freight.kappa": 0.4, "freight.gamma": 0.25}


def test_profile_version_v4_sets_interim_freight_values_and_is_active():
    """v4 = v3 with four interim freight pass-through values for the demo, pending calibration (P93)."""
    from hlag_engine.company_profile.schema import ACTIVE_SETTING_PATH, PROFILE_DIR, active_profile_path, load_profile

    v3 = load_profile(PROFILE_DIR / "company_profile_2026H1-v3.yaml")
    v4 = load_profile(PROFILE_DIR / "company_profile_2026H1-v4.yaml")
    assert v4.profile_version == "2026H1-v4"
    assert active_profile_path(ACTIVE_SETTING_PATH).name == "company_profile_2026H1-v4.yaml"
    for key, value in INTERIM_FREIGHT.items():
        entry = v4.parameters[key]
        assert entry.value == value and entry.label.value == "ASSUMPTION"
        assert entry.rationale == "interim, pending calibration (P93)"
        low, high = entry.allowed_range
        assert low <= value <= high
        assert entry.model_copy(update={"value": v3.parameters[key].value, "rationale": v3.parameters[key].rationale}) == v3.parameters[key]
    assert {k for k in v4.parameters if v4.parameters[k] != v3.parameters[k]} == set(INTERIM_FREIGHT)
    assert v4.anchor_snapshot == v3.anchor_snapshot
