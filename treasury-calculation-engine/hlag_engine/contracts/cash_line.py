"""Cash line contract (spec 2.1-2.3)."""

import math
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, PassType, ProvenanceLabel

LineKey = tuple[OwnerSpec, LineCode, date, Currency, str]

CENT = Decimal("0.01")


def round_to_cents(value: float | int | Decimal) -> Decimal:
    """Convert an engine amount to a stored Decimal rounded to cents (2.1, D33).

    A float goes through its shortest decimal repr, so binary noise (0.1 + 0.2) does not
    leak into the Decimal. Rounding uses Decimal's default, ROUND_HALF_EVEN.
    """
    if isinstance(value, bool):
        raise TypeError("an amount cannot be a bool")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("an amount must be finite")
        value = Decimal(repr(value))
    return Decimal(value).quantize(CENT)


class CashLine(ContractModel):
    """One dated cash movement in native currency.

    Sign convention (2.3): cash in is positive, cash out negative. Modules fill the
    identity and amount fields; the orchestrator fills pass_type, event_ids, run_id and
    scenario_id; the FX conversion layer fills the four conversion fields.

    Amounts (D33): the engine calculates in float64 and builds CashLines when it stores
    them. The three amount fields (amount, amount_usd_base, amount_usd_scenario) are stored
    as Decimal rounded to cents: a float is converted and rounded with round_to_cents(); a
    Decimal, int or string must already be in whole cents and is rejected otherwise, so
    stored values are never silently changed. FX rates are not rounded.
    """

    sub_id: str = Field(min_length=1)
    date: date
    currency: Currency
    amount: Decimal
    owner_spec: OwnerSpec
    line_code: LineCode
    label: ProvenanceLabel
    pass_type: PassType | None = None
    event_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    scenario_id: str | None = None
    entity: str | None = None  # reserved; one entity (HLAG_GROUP) in v1
    parent_id: str | None = None  # reserved; no multi-leg instruments in v1
    fx_rate_base: Decimal | None = Field(default=None, gt=0)  # USD per one unit of currency
    fx_rate_scenario: Decimal | None = Field(default=None, gt=0)
    amount_usd_base: Decimal | None = None
    amount_usd_scenario: Decimal | None = None

    @field_validator("amount", "amount_usd_base", "amount_usd_scenario", mode="before")
    @classmethod
    def _to_cents(cls, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return value  # None is allowed where optional; bool is rejected by the type check
        if isinstance(value, float):
            return round_to_cents(value)
        if isinstance(value, (int, str, Decimal)):
            try:
                exact = Decimal(value)
            except ArithmeticError:
                return value  # let pydantic report the invalid number
            if not exact.is_finite():
                raise ValueError("an amount must be finite")
            if exact != exact.quantize(CENT):
                raise ValueError(f"amount {value} is not in whole cents; use round_to_cents()")
            return exact.quantize(CENT)
        return value

    @model_validator(mode="after")
    def _check_code_owner_currency(self) -> "CashLine":
        code = self.line_code
        if not code.owners:
            raise ValueError(f"line_code {code.value} is never emitted as a cash line (2.2)")
        if self.owner_spec not in code.owners:
            allowed = ", ".join(sorted(o.value for o in code.owners))
            raise ValueError(
                f"owner_spec {self.owner_spec.value} may not emit {code.value} (allowed: {allowed})"
            )
        # 2.2: DEBT_PRINCIPAL is rates (USD) or fx (EUR); DEBT_DRAWDOWN is always USD.
        if code is LineCode.DEBT_PRINCIPAL:
            expected = Currency.USD if self.owner_spec is OwnerSpec.RATES else Currency.EUR
            if self.currency is not expected:
                raise ValueError(
                    f"DEBT_PRINCIPAL from {self.owner_spec.value} must be in {expected.value}"
                )
        if code is LineCode.DEBT_DRAWDOWN and self.currency is not Currency.USD:
            raise ValueError("DEBT_DRAWDOWN must be in USD (2.2)")
        if code is LineCode.ACQUISITION_PAYMENT and self.currency is not Currency.USD:
            raise ValueError("ACQUISITION_PAYMENT must be in USD (2.2, P63)")
        if self.currency is Currency.USD:
            for name in ("fx_rate_base", "fx_rate_scenario"):
                rate = getattr(self, name)
                if rate is not None and rate != 1:
                    raise ValueError(f"{name} must be 1.0 for USD lines")
        return self

    @property
    def line_key(self) -> LineKey:
        """Stable pairing key (2.1): (owner_spec, line_code, date, currency, sub_id)."""
        return (self.owner_spec, self.line_code, self.date, self.currency, self.sub_id)
