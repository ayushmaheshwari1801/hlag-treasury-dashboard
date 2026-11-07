import pytest

from hlag_engine.contracts.enums import (
    BaselineTreatment,
    EventType,
    LineCode,
    OwnerSpec,
    PresetId,
    PresetScope,
    ProvenanceLabel,
)


def test_event_type_is_the_closed_2_8_list():
    expected = {
        "central_bank_decision", "bank_failure", "credit_event", "rating_change",
        "financing_news", "regulation", "sanction", "trade_disruption", "port_disruption",
        "commodity_move", "fx_policy", "capital_control", "payment_rule", "tax_rule",
        "labour_action", "market_structure", "contract_season", "competitor_action",
        "company_announcement", "supplier_news", "fleet_change", "other",
    }  # fmt: skip
    assert {e.value for e in EventType} == expected


def test_every_line_code_has_treatment_and_owner_entry():
    for code in LineCode:
        assert isinstance(code.baseline_treatment, BaselineTreatment)
        assert isinstance(code.owners, frozenset)


@pytest.mark.parametrize(
    ("code", "treatment"),
    [
        (LineCode.FREIGHT_REVENUE, BaselineTreatment.DELTA_ONLY),
        (LineCode.FUEL_PURCHASE, BaselineTreatment.DELTA_ONLY),
        (LineCode.DEBT_PRINCIPAL, BaselineTreatment.BASE_AMOUNT),
        (LineCode.DIVIDEND, BaselineTreatment.BASE_AMOUNT),
        (LineCode.INTEREST_EXPENSE, BaselineTreatment.PER_INTEREST_IN_OCF),
        (LineCode.FX_TRANSACTION, BaselineTreatment.PER_CAPEX_BASELINE),
        (LineCode.FX_POSITION, BaselineTreatment.MEMO),
        (LineCode.FX_CONVERSION_EFFECT, BaselineTreatment.AGGREGATION_ONLY),
        (LineCode.ACQUISITION_PAYMENT, BaselineTreatment.BASE_AMOUNT),
    ],
)
def test_baseline_treatment(code, treatment):
    assert code.baseline_treatment is treatment


def test_weakest_label_rule():
    L = ProvenanceLabel
    assert L.weakest(L.MARKET, L.ASSUMPTION, L.DISCLOSED) is L.ASSUMPTION
    assert L.weakest(L.MARKET, L.DERIVED) is L.DERIVED
    assert L.weakest(L.DISCLOSED) is L.DISCLOSED
    assert [label.rank for label in (L.ASSUMPTION, L.PROXY, L.DERIVED, L.MARKET, L.DISCLOSED)] == [
        0, 1, 2, 3, 4,
    ]  # fmt: skip
    with pytest.raises(ValueError):
        L.weakest()


def test_combined_label_is_rejected():
    with pytest.raises(ValueError):
        ProvenanceLabel("ASSUMPTION / DERIVED")


def test_preset_ids_and_scopes():
    assert len(PresetId) == 29
    assert PresetId.RED_SEA_REESCALATION.scope is PresetScope.JOINT
    assert PresetId.CREDIT_SQUEEZE.scope is PresetScope.RATES
    assert PresetId.HISTORY_REPLAY.scope is PresetScope.FREIGHT
    assert PresetId.BUNKER_PRICE_REVERSAL.scope is PresetScope.BUNKER
    assert PresetId.CNY_DEVALUATION.scope is PresetScope.FX
    assert PresetId.RATES_UP_100BP.scope.driver is OwnerSpec.RATES
    assert PresetId.FX_BRL_DOWN_10.scope.driver is OwnerSpec.FX
    assert PresetScope.JOINT.driver is None
