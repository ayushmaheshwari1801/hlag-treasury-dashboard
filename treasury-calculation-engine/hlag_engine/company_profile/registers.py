"""Reference registers (spec 3.4, D22, D27). They answer "does this news touch us, and where?"
and never create cash lines on their own.
"""

import math
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import AfterValidator, Field, field_validator, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import Currency, ProvenanceLabel, Trade
from hlag_engine.contracts.iso_codes import ISO_3166_ALPHA2, ISO_4217
from hlag_engine.company_profile.schema import EntryStatus, Source

REGISTER_DIR = Path(__file__).parent / "registers"
SHARE_TOLERANCE = 1e-9


class _Aliased(ContractModel):
    """Generic names (P20) with optional real-name aliases, labelled ASSUMPTION when filled."""

    id: str = Field(pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    aliases_label: ProvenanceLabel | None = None

    @model_validator(mode="after")
    def _alias_label(self) -> "_Aliased":
        if self.aliases and self.aliases_label is not ProvenanceLabel.ASSUMPTION:
            raise ValueError(f"{self.id}: filled aliases must be labelled ASSUMPTION")
        if not self.aliases and self.aliases_label is not None:
            raise ValueError(f"{self.id}: aliases_label set without aliases")
        return self

    def matches(self, text: str) -> bool:
        needle = text.strip().casefold()
        return any(needle == n.casefold() for n in (self.id, self.name, *self.aliases))


def _country(code: str) -> str:
    if code not in ISO_3166_ALPHA2:
        raise ValueError(f"{code} is not an ISO 3166-1 alpha-2 code")
    return code


CountryCode = Annotated[str, AfterValidator(_country)]


class Counterparty(_Aliased):
    country: CountryCode
    credit_line_share: float = Field(ge=0, le=1)  # share of T2
    deposit_share: float = Field(ge=0, le=1)  # share of T1
    hedge_counterparty: bool


class CounterpartyRegister(ContractModel):
    label: ProvenanceLabel
    note: str
    counterparties: list[Counterparty] = Field(min_length=6, max_length=10)

    @model_validator(mode="after")
    def _shares(self) -> "CounterpartyRegister":
        ids = [c.id for c in self.counterparties]
        if len(ids) != len(set(ids)):
            raise ValueError("counterparty ids must be unique")
        for field in ("credit_line_share", "deposit_share"):
            total = sum(getattr(c, field) for c in self.counterparties)
            if not math.isclose(total, 1.0, abs_tol=SHARE_TOLERANCE):
                raise ValueError(f"{field} must sum to 100% across counterparties, got {total}")
        return self

    def find(self, text: str) -> Counterparty | None:
        return next((c for c in self.counterparties if c.matches(text)), None)


class LabelledText(ContractModel):
    value: str
    label: ProvenanceLabel
    source: Source | None = None


class FootprintCountry(ContractModel):
    code: CountryCode
    currencies: list[Currency]
    payment_rails: list[str]
    label: ProvenanceLabel


class CashLocationShares(ContractModel):
    label: ProvenanceLabel
    note: str
    shares: dict[Currency, float]

    @model_validator(mode="after")
    def _sum(self) -> "CashLocationShares":
        if not math.isclose(sum(self.shares.values()), 1.0, abs_tol=SHARE_TOLERANCE):
            raise ValueError("cash location shares must sum to 100%")
        return self


class OperatingFootprint(ContractModel):
    cash_structure: LabelledText
    countries: list[FootprintCountry]
    cash_location_shares: CashLocationShares


class TradeEntry(ContractModel):
    trade: Trade
    name: str
    name_label: ProvenanceLabel
    source: Source
    wci_route: str
    route_label: ProvenanceLabel
    main_ports: list[str]
    ports_label: ProvenanceLabel


class TradesRegister(ContractModel):
    trades: list[TradeEntry]

    @model_validator(mode="after")
    def _all_trades(self) -> "TradesRegister":
        if sorted(t.trade for t in self.trades) != sorted(Trade):
            raise ValueError("the trades register must list each trade exactly once")
        return self


class FleetNote(ContractModel):
    text: str
    label: ProvenanceLabel


class FleetScrubber(ContractModel):
    profile_key: str
    label: ProvenanceLabel
    note: str


class FleetRegister(ContractModel):
    scrubber_share: FleetScrubber
    notes: list[FleetNote] = Field(default_factory=list)


class Hub(ContractModel):
    id: str
    name: str
    country: CountryCode
    label: ProvenanceLabel


class Supplier(_Aliased):
    hubs: list[str] = Field(min_length=1)


class SuppliersRegister(ContractModel):
    hubs: list[Hub]
    suppliers: list[Supplier]

    @model_validator(mode="after")
    def _hubs_known(self) -> "SuppliersRegister":
        known = {h.id for h in self.hubs}
        for s in self.suppliers:
            unknown = set(s.hubs) - known
            if unknown:
                raise ValueError(f"supplier {s.id} names unknown hubs {sorted(unknown)}")
        return self

    def find(self, text: str) -> Supplier | None:
        return next((s for s in self.suppliers if s.matches(text)), None)


class Regime(ContractModel):
    id: str
    name: str
    countries: list[CountryCode] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=list)
    label: ProvenanceLabel
    source: Source | None = None  # required when DISCLOSED (report and page)

    @model_validator(mode="after")
    def _disclosed_source(self) -> "Regime":
        if self.label is ProvenanceLabel.DISCLOSED and self.source is None:
            raise ValueError(f"DISCLOSED regime {self.id} needs a source")
        return self

    @field_validator("currencies")
    @classmethod
    def _currencies(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in ISO_4217]
        if bad:
            raise ValueError(f"not ISO 4217 codes: {bad}")
        return v


class RegulatoryRegister(ContractModel):
    status: EntryStatus = EntryStatus.OK
    note: str | None = None
    regimes: list[Regime]


class Registers(ContractModel):
    counterparties: CounterpartyRegister
    operating_footprint: OperatingFootprint
    trades: TradesRegister
    fleet: FleetRegister
    suppliers: SuppliersRegister
    regulatory: RegulatoryRegister

    def availability_keys(self, tiers: list[str]) -> list[str]:
        """liquidity.<tier>.<counterparty> keys (3.7)."""
        return [f"liquidity.{t}.{c.id}" for t in tiers for c in self.counterparties.counterparties]


_FILES = {
    "counterparties": "counterparties.yaml",
    "operating_footprint": "operating_footprint.yaml",
    "trades": "trades.yaml",
    "fleet": "fleet.yaml",
    "suppliers": "suppliers.yaml",
    "regulatory": "regulatory.yaml",
}


def load_registers(directory: Path | str = REGISTER_DIR) -> Registers:
    raw: dict[str, Any] = {}
    for name, filename in _FILES.items():
        with open(Path(directory) / filename, encoding="utf-8") as handle:
            raw[name] = yaml.safe_load(handle)
    return Registers.model_validate(raw)
