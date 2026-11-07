"""Deterministic exposure generator and the single maturity schedule (3.5, 3.7, D29, D44)."""

import re
from datetime import date

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import Adjustment
from hlag_engine.contracts.enums import Currency, OwnerSpec, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem, ExposureRegister
from hlag_engine.generator import generate
from hlag_engine.generator.calendar import add_months, horizon, month_end
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.maturities import build_maturity_schedule
from hlag_engine.generator.recipes import quarter_volume
from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.schema import DEFAULT_PROFILE_PATH, profile_from_dict
from tests.conftest import T0, complete_raw

EUR_SHARE = (1_999.1 / 2_750.1) * 0.30  # the EUR bullet bond is not in the maturity slices (P49)


@pytest.fixture(scope="module")
def register(profile):
    return generate(profile, T0)


def items(register, owner, kind):
    return register.by_kind(owner, kind)


# --- determinism and shape -------------------------------------------------------------------


def test_deterministic(profile):
    assert generate(profile, T0) == generate(profile, T0)
    assert generate(profile, T0).model_dump_json() == generate(profile_from_dict(complete_raw()), T0).model_dump_json()


def test_complete_with_fixture_profile(register):
    assert register.complete, register.missing
    assert (register.horizon_start, register.horizon_end) == (date(2026, 11, 1), date(2027, 10, 31))


def test_every_item_is_synthetic_labelled_and_well_formed(register):
    pattern = re.compile(r"^(freight|bunker|rates|fx):[a-z_]+:.+$")
    for item in register.items:
        assert item.synthetic is True
        assert pattern.match(item.sub_id)
        assert item.sub_id.split(":")[0] == item.owner_spec.value
        assert item.sizing_note
        assert isinstance(item.label, ProvenanceLabel)


def test_sub_ids_unique(register):
    ids = [i.sub_id for i in register.items]
    assert len(ids) == len(set(ids))


def test_register_records_profile(register, profile):
    assert register.profile_version == profile.profile_version
    assert register.profile_hash == profile.content_hash


def test_exposure_item_rejects_mismatched_sub_id():
    with pytest.raises(ValidationError, match="owner_spec"):
        ExposureItem(sub_id="rates:float:USD:1", owner_spec="fx", kind="float", label="ASSUMPTION", sizing_note="n")
    with pytest.raises(ValidationError, match="kind"):
        ExposureItem(sub_id="rates:float:USD:1", owner_spec="rates", kind="fixed", label="ASSUMPTION", sizing_note="n")
    with pytest.raises(ValidationError):
        ExposureItem(sub_id="rates float", owner_spec="rates", kind="float", label="ASSUMPTION", sizing_note="n")


def test_register_rejects_duplicate_sub_ids(register):
    dup = register.items[0]
    with pytest.raises(ValidationError, match="duplicate"):
        ExposureRegister(**{**register.model_dump(), "items": [dup.model_dump(), dup.model_dump()]})


def test_horizon_rules():
    assert horizon(date(2026, 10, 31)) == (date(2026, 11, 1), date(2027, 10, 31))
    assert horizon(date(2026, 11, 15)) == (date(2026, 11, 16), date(2027, 11, 30))
    assert horizon(date(2028, 2, 29)) == (date(2028, 3, 1), date(2029, 2, 28))


# --- maturity schedule (single, D29, D44, P18) ---------------------------------------------


def test_maturity_schedule(profile):
    schedule = build_maturity_schedule(Inputs(RunContext(profile, T0)), date(2027, 10, 31))
    assert schedule.annual_usd == pytest.approx(1_200_000_000)
    assert schedule.monthly_usd == pytest.approx(100_000_000)
    assert schedule.eur_share == pytest.approx(EUR_SHARE)
    assert schedule.label is ProvenanceLabel.ASSUMPTION
    dates = [s.date for s in schedule.slices]
    expected = [month_end(add_months(date(2026, 11, 1), i)) for i in range(12)]
    assert dates == expected  # Jul-Oct 2026 absorbed by roll-forward; Jul-Oct 2027 continue
    for s in schedule.slices:
        assert s.total_usd == pytest.approx(100_000_000)
        assert s.usd_part_usd + s.eur_part_usd == pytest.approx(s.total_usd)


def test_maturities_split_between_rates_and_fx(register):
    usd = items(register, OwnerSpec.RATES, "maturity")
    eur = items(register, OwnerSpec.FX, "maturity")
    assert len(usd) == len(eur) == 12
    for u, e in zip(usd, eur):
        assert u.dates["maturity_date"] == e.dates["maturity_date"]
        assert u.currency is Currency.USD and e.currency is Currency.EUR
        total = u.quantities["amount"] + e.quantities["amount_usd_at_disclosure"]
        assert total == pytest.approx(100_000_000)
        assert e.quantities["amount"] == pytest.approx(e.quantities["amount_usd_at_disclosure"] / 1.25)


def test_maturities_continue_after_june_2027(register):
    dates = [i.dates["maturity_date"] for i in items(register, OwnerSpec.RATES, "maturity")]
    assert date(2027, 7, 31) in dates and date(2027, 10, 31) in dates
    assert all(d > T0 for d in dates)


def test_lease_working_default_when_current_leases_not_entered():
    raw = complete_raw()
    raw["parameters"]["debt.current_lease_liabilities_usd"].update(value=None, status="to_enter")
    p = profile_from_dict(raw)
    schedule = build_maturity_schedule(Inputs(RunContext(p, T0)), date(2027, 10, 31))
    assert schedule.annual_usd == pytest.approx(1_852_000_000 - 1_200_000_000)
    assert "working default" in schedule.lease_basis


def test_mid_month_t0_drops_slices_on_or_before_t0(profile):
    as_of = date(2027, 1, 31)
    schedule = build_maturity_schedule(Inputs(RunContext(profile, as_of)), horizon(as_of)[1])
    assert schedule.slices[0].date == date(2027, 2, 28)
    assert schedule.slices[-1].date == date(2028, 1, 31)


# --- freight ------------------------------------------------------------------------------------


def test_freight_volumes_per_trade_and_month(register, profile):
    volumes = items(register, OwnerSpec.FREIGHT, "volume")
    assert len(volumes) == 4 * 12
    nov = [v for v in volumes if v.dates["month"] == date(2026, 11, 1)]
    total_nov = sum(v.quantities["teu"] for v in nov)
    # Q4 2025 group volume x (1 + g) x 30 days / 92 days in Q4 2026
    assert total_nov == pytest.approx(3_100_000 * 1.02 * 30 / 92)
    ae = next(v for v in nov if v.sub_id == "freight:volume:ASIA_EUROPE:2026-11")
    assert ae.quantities["teu"] == pytest.approx(total_nov * 1_866 / 6_683)
    assert ae.label is ProvenanceLabel.ASSUMPTION


def test_unpublished_quarter_uses_latest_same_quarter_times_growth(profile):
    inputs = Inputs(RunContext(profile, T0))
    value, label, source = quarter_volume(inputs, 2026, 3)
    assert value == pytest.approx(3_000_000 * 1.02)
    assert label is ProvenanceLabel.ASSUMPTION and "2025Q3" in source
    value2, _, _ = quarter_volume(inputs, 2027, 4)
    assert value2 == pytest.approx(3_100_000 * 1.02**2)
    published, label3, _ = quarter_volume(inputs, 2026, 1)
    assert published == 3_200_000 and label3 is ProvenanceLabel.DISCLOSED


def test_q3_2027_volume_uses_estimated_q3_2026(register):
    jul = [v for v in items(register, OwnerSpec.FREIGHT, "volume") if v.dates["month"] == date(2027, 7, 1)]
    assert sum(v.quantities["teu"] for v in jul) == pytest.approx(3_000_000 * 1.02 * 1.02 * 31 / 92)


def test_contract_cohorts(register, profile):
    cohorts = items(register, OwnerSpec.FREIGHT, "contract")
    by_trade = {}
    for c in cohorts:
        by_trade.setdefault(c.sub_id.split(":")[2], []).append(c)
    assert len(by_trade["ASIA_AMERICA"]) == 1
    assert by_trade["ASIA_AMERICA"][0].dates["reset_date"] == date(2027, 5, 1)
    for trade in ("ASIA_EUROPE", "EUROPE_AMERICA", "AFRICA_INTRA"):
        resets = [c.dates["reset_date"] for c in by_trade[trade]]
        assert resets == [add_months(date(2026, 11, 1), i) for i in range(12)]
        assert sum(c.quantities["share"] for c in by_trade[trade]) == pytest.approx(profile.parameters["freight.contract_share"].value)


def test_volume_class_shares_sum_to_one(register):
    for trade in ("ASIA_EUROPE", "ASIA_AMERICA"):
        spot = next(i for i in register.items if i.sub_id == f"freight:spot:{trade}")
        index = next(i for i in register.items if i.sub_id == f"freight:index:{trade}")
        contract = sum(c.quantities["share"] for c in items(register, OwnerSpec.FREIGHT, "contract")
                       if c.sub_id.split(":")[2] == trade)  # fmt: skip
        assert spot.quantities["share"] + index.quantities["share"] + contract == pytest.approx(1.0)


# --- bunker ---------------------------------------------------------------------------------------


def test_bunker_purchases_sum_to_monthly_consumption(register):
    purchases = items(register, OwnerSpec.BUNKER, "purchase")
    assert len(purchases) == 12 * 4
    nov = [p for p in purchases if p.dates["month"] == date(2026, 11, 1)]
    assert sum(p.quantities["tonnes"] for p in nov) == pytest.approx(400_000)
    sin_hsfo = next(p for p in nov if p.sub_id == "bunker:purchase:SIN:HSFO:2026-11")
    assert sin_hsfo.quantities["tonnes"] == pytest.approx(400_000 * 0.60 * 0.15)


def test_time_varying_adjustment_reaches_register_from_effective_month(profile):
    adj = Adjustment(
        adjustment_id="a", param_key="bunker.scrubber_share", old_value=0.15, new_value=0.05, step="large",
        effective_date=date(2027, 3, 1), review_date=date(2027, 8, 1), event_ids=["e"], justification="j",
        confidence=0.8, proposed_by="agent", status="approved", approver="t",
        decided_at="2026-10-01T00:00:00+00:00",
    )  # fmt: skip
    reg = generate(profile, T0, RunContext(profile, T0, (adj,)))

    def hsfo(month):
        return sum(p.quantities["tonnes"] for p in reg.by_kind(OwnerSpec.BUNKER, "purchase")
                   if p.dates["month"] == month and ":HSFO:" in p.sub_id)  # fmt: skip

    assert hsfo(date(2027, 2, 1)) == pytest.approx(400_000 * 0.15)
    assert hsfo(date(2027, 3, 1)) == pytest.approx(400_000 * 0.05)
    assert hsfo(date(2027, 8, 1)) == pytest.approx(400_000 * 0.15)


# --- rates ----------------------------------------------------------------------------------------


def test_floating_tranches(register):
    floats = items(register, OwnerSpec.RATES, "float")
    usd = [f for f in floats if f.currency is Currency.USD]
    eur = [f for f in floats if f.currency is Currency.EUR]
    assert len(usd) == len(eur) == 3
    assert sum(f.quantities["notional"] for f in usd) == pytest.approx(1_999_100_000 * 1.2 * 0.70)
    assert sum(f.quantities["notional"] for f in eur) == pytest.approx(1_999_100_000 * 0.30)
    first_resets = [f.dates["resets"][0] for f in usd]
    assert first_resets == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]
    assert usd[0].dates["resets"][:2] == [date(2026, 11, 1), date(2027, 2, 1)]


def test_fixed_debt_lease_deposits_zim(register):
    by_id = {i.sub_id: i for i in register.items}
    # semi-annual coupons from the active profile (2026H1-v2, P60)
    assert by_id["rates:fixed:EUR:bond"].quantities == {"notional": 300_000_000, "coupon_pct": 2.5, "coupons_per_year": 2}
    assert by_id["rates:fixed:EUR:bond"].dates["coupons"] == [date(2027, 4, 15), date(2027, 10, 15)]
    assert by_id["rates:fixed:USD:1"].quantities["notional"] == pytest.approx((2_750.1e6 - 1_999.1e6 - 300e6) * 1.2)
    assert by_id["rates:lease:USD:LEASE"].quantities["monthly_interest"] == pytest.approx(207.1e6 * 1.2 / 12)
    deposits = items(register, OwnerSpec.RATES, "deposit")
    assert sum(d.quantities["balance_usd"] for d in deposits) == pytest.approx(3_150e6 * 0.85)
    zim = by_id["rates:zim:USD:1"]
    assert zim.quantities["switch_default"] is False and zim.dates["draw_date"] == date(2026, 12, 31)
    assert zim.quantities["notional"] == 2_500_000_000 and zim.quantities["consideration_usd"] == 4_200_000_000


def test_v1_profile_zim_consideration_falls_back_to_the_financing(v1_profile):
    """2026H1-v1 has no rates.zim_consideration_usd: the whole consideration counts as financed (P63)."""
    raw_items = generate(profile_from_dict(complete_raw(DEFAULT_PROFILE_PATH)), T0).items
    zim = {i.sub_id: i for i in raw_items}["rates:zim:USD:1"]
    assert zim.quantities["notional"] == zim.quantities["consideration_usd"] == 4_200_000_000


# --- fx --------------------------------------------------------------------------------------------


def test_fx_items(register):
    opex = items(register, OwnerSpec.FX, "opex")
    cad = [o for o in opex if o.currency is Currency.CAD]
    assert len(cad) == 52 and all(o.dates["payment_date"].weekday() == 4 for o in cad)
    assert cad[0].quantities["amount"] == 5_540_000
    by_id = {i.sub_id: i for i in register.items}
    assert by_id["fx:dividend:EUR:2027-05-10"].quantities["amount"] == 500_000_000
    inr = [o for o in items(register, OwnerSpec.FX, "obligation") if o.currency is Currency.INR]
    assert len(inr) == 12 and sum(o.quantities["amount"] for o in inr) == pytest.approx(1.4e9)
    assert inr[0].dates["payment_date"] == date(2026, 11, 30)
    assert by_id["fx:memo:GBP:1"].quantities["position_usd"] == -24_000_000


def test_obligation_shift_moves_dates(profile):
    ctx = RunContext(profile, T0, overrides={"fx.obligation_shift_months": 3})
    reg = generate(profile, T0, ctx)
    brl = [o for o in reg.by_kind(OwnerSpec.FX, "obligation") if o.currency is Currency.BRL]
    assert brl[0].dates["payment_date"] == date(2027, 2, 28)


# --- placeholders in the real profile -----------------------------------------------------------


def test_active_profile_has_no_missing_inputs(real_profile):
    reg = generate(real_profile, T0)
    assert reg.missing == [] and reg.complete


def test_real_profile_reports_missing_inputs(v1_profile):
    reg = generate(v1_profile, T0)
    keys = {m.key for m in reg.missing}
    assert keys == {
        "liquidity.annual_lease_repayments_usd",
        "freight.group_volume_teu.2025Q3",
        "freight.group_volume_teu.2025Q4",
        "freight.group_volume_teu.2026Q1",
        "freight.group_volume_teu.2026Q2",
        "fx.usd_per_eur_close_2025_12_31",
        "fx.dividend_reference_eur",
    }
    assert not reg.complete
    assert items(reg, OwnerSpec.BUNKER, "purchase")  # unaffected parts are still built
    assert items(reg, OwnerSpec.RATES, "maturity") == []


def test_ctx_must_match(profile):
    with pytest.raises(ValueError):
        generate(profile, T0, RunContext(profile, date(2026, 12, 31)))
