"""Reference registers (3.4, P20)."""

import shutil

import pytest
from pydantic import ValidationError

from hlag_engine.contracts.enums import Trade
from hlag_engine.company_profile.registers import REGISTER_DIR, Counterparty, CounterpartyRegister, Supplier, load_registers


def test_registers_load(registers):
    assert len(registers.counterparties.counterparties) == 8
    assert {t.trade for t in registers.trades.trades} == set(Trade)
    assert registers.operating_footprint.cash_structure.label.value == "DISCLOSED"
    assert {h.id for h in registers.suppliers.hubs} == {"SIN", "RTM"}


def test_generic_names_and_empty_aliases(registers):
    for c in registers.counterparties.counterparties:
        assert c.id.startswith("BANK_") and c.aliases == []
    for s in registers.suppliers.suppliers:
        assert s.id.startswith("SUPPLIER_") and s.aliases == []


def test_shares_sum_to_one_per_tier(registers):
    cps = registers.counterparties.counterparties
    assert sum(c.credit_line_share for c in cps) == pytest.approx(1.0)
    assert sum(c.deposit_share for c in cps) == pytest.approx(1.0)


def test_shares_not_summing_rejected(registers):
    data = registers.counterparties.model_dump()
    data["counterparties"][0]["deposit_share"] += 0.05
    with pytest.raises(ValidationError, match="deposit_share must sum"):
        CounterpartyRegister.model_validate(data)


def test_register_needs_six_to_ten_banks(registers):
    data = registers.counterparties.model_dump()
    data["counterparties"] = data["counterparties"][:5]
    with pytest.raises(ValidationError):
        CounterpartyRegister.model_validate(data)


def bank(**overrides):
    data = {"id": "BANK_Z", "name": "BANK_Z", "country": "DE", "credit_line_share": 0.1,
            "deposit_share": 0.1, "hedge_counterparty": False}  # fmt: skip
    data.update(overrides)
    return Counterparty(**data)


def test_filled_aliases_must_be_labelled_assumption():
    with pytest.raises(ValidationError, match="ASSUMPTION"):
        bank(aliases=["Example Bank AG"])
    with pytest.raises(ValidationError, match="ASSUMPTION"):
        bank(aliases=["Example Bank AG"], aliases_label="DISCLOSED")
    b = bank(aliases=["Example Bank AG"], aliases_label="ASSUMPTION")
    assert b.matches("example bank ag") and b.matches("bank_z")
    with pytest.raises(ValidationError):
        bank(aliases_label="ASSUMPTION")  # label without aliases


@pytest.mark.parametrize("country", ["UK", "DEU", "xx"])
def test_bad_country_rejected(country):
    with pytest.raises(ValidationError):
        bank(country=country)


def test_find_by_id_name_or_alias(registers):
    assert registers.counterparties.find("bank_d").id == "BANK_D"
    assert registers.counterparties.find("Unknown Bank") is None
    data = registers.counterparties.model_dump()
    data["counterparties"][2]["aliases"] = ["Example Bank SA"]
    data["counterparties"][2]["aliases_label"] = "ASSUMPTION"
    reg = CounterpartyRegister.model_validate(data)
    assert reg.find("EXAMPLE BANK SA").id == "BANK_C"


def test_supplier_unknown_hub_rejected(registers):
    data = registers.suppliers.model_dump()
    data["suppliers"].append({"id": "SUPPLIER_Z", "name": "SUPPLIER_Z", "hubs": ["HOU"]})
    with pytest.raises(ValidationError, match="unknown hubs"):
        type(registers.suppliers).model_validate(data)
    assert Supplier(id="SUPPLIER_Z", name="SUPPLIER_Z", hubs=["SIN"]).hubs == ["SIN"]


def test_availability_keys(registers):
    keys = registers.availability_keys(["T1", "T2"])
    assert len(keys) == 16
    assert "liquidity.T1.BANK_A" in keys and "liquidity.T2.BANK_H" in keys


def test_regulatory_register_checked_against_ar(registers):
    reg = registers.regulatory
    assert reg.status.value == "ok"
    by_id = {r.id: r for r in reg.regimes}
    disclosed = {i for i, r in by_id.items() if r.label.value == "DISCLOSED"}
    assert disclosed == {"EU_ETS", "FUELEU_MARITIME", "ISM_CODE", "ISPS_CODE", "MLC", "ADVANCE_MANIFEST",
                         "EMBARGO_SANCTIONS", "IMO_CII"}
    assert by_id["EU_ETS"].source.page == "24" and by_id["ISM_CODE"].source.page == "23"
    assert by_id["IMO_RULES"].label.value == "ASSUMPTION" and by_id["IMO_RULES"].source is None


def test_disclosed_regime_needs_source():
    from pydantic import ValidationError

    from hlag_engine.company_profile.registers import Regime

    with pytest.raises(ValidationError, match="needs a source"):
        Regime(id="X", name="X", label="DISCLOSED")


def test_trades_register_links_wci_routes(registers):
    routes = {t.trade: t.wci_route for t in registers.trades.trades}
    assert routes[Trade.ASIA_EUROPE] == "WCI_SHA_RTM"
    assert routes[Trade.AFRICA_INTRA] == "WCI_COMPOSITE"


def test_load_registers_from_directory(tmp_path, registers):
    for f in REGISTER_DIR.iterdir():
        shutil.copy(f, tmp_path / f.name)
    assert load_registers(tmp_path) == registers
