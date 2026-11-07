"""Exposure register contract (spec 3.5, 5.1). Built by the generator; static only, no shocks.

Units follow P1; amounts are in the native currency named by ``currency`` unless the
quantity name says otherwise (``*_usd``, ``*_eur``, ``teu``, ``tonnes``).
"""

from datetime import date

from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import Currency, OwnerSpec, ProvenanceLabel

SUB_ID_PATTERN = r"^(freight|bunker|rates|fx):[a-z_]+:[A-Za-z0-9_.:-]+$"

Quantity = float | int | str | bool
DateValue = date | list[date]


class ExposureItem(ContractModel):
    """One static exposure with exactly one owner (3.5)."""

    sub_id: str = Field(pattern=SUB_ID_PATTERN)  # <module>:<kind>:<key>
    owner_spec: OwnerSpec
    kind: str = Field(pattern=r"^[a-z_]+$")
    currency: Currency | None = None  # None for physical quantities (TEU, tonnes)
    quantities: dict[str, Quantity] = Field(default_factory=dict)
    dates: dict[str, DateValue] = Field(default_factory=dict)
    label: ProvenanceLabel  # label of the figure that sized it
    synthetic: bool = True
    sizing_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _sub_id_matches(self) -> "ExposureItem":
        module, kind, _ = self.sub_id.split(":", 2)
        if module != self.owner_spec.value:
            raise ValueError(f"sub_id module {module!r} differs from owner_spec {self.owner_spec.value!r}")
        if kind != self.kind:
            raise ValueError(f"sub_id kind {kind!r} differs from kind {self.kind!r}")
        return self


class MissingInput(ContractModel):
    """A profile value the generator needed but found as a placeholder."""

    key: str
    status: str
    affects: str  # which items were skipped


class ExposureRegister(ContractModel):
    as_of: date
    horizon_start: date
    horizon_end: date
    profile_version: str
    profile_hash: str
    items: list[ExposureItem] = Field(default_factory=list)
    missing: list[MissingInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_sub_ids(self) -> "ExposureRegister":
        seen: set[str] = set()
        for item in self.items:
            if item.sub_id in seen:
                raise ValueError(f"duplicate sub_id {item.sub_id}")
            seen.add(item.sub_id)
        return self

    def by_owner(self, owner: OwnerSpec) -> list[ExposureItem]:
        return [i for i in self.items if i.owner_spec is owner]

    def by_kind(self, owner: OwnerSpec, kind: str) -> list[ExposureItem]:
        return [i for i in self.items if i.owner_spec is owner and i.kind == kind]

    @property
    def complete(self) -> bool:
        return not self.missing
