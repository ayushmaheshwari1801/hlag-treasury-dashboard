"""Pairing (spec 2.1, 4.1, D17): base and scenario lines on the stable key, full outer join, missing side = 0."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from hlag_engine.contracts.cash_line import CashLine

ZERO = Decimal(0)


@dataclass(frozen=True)
class Pair:
    key: tuple
    base: CashLine | None
    scen: CashLine | None

    @property
    def ref(self) -> CashLine:
        return self.base or self.scen  # type: ignore[return-value]

    @property
    def a(self) -> Decimal:
        return self.base.amount if self.base else ZERO

    @property
    def b(self) -> Decimal:
        return self.scen.amount if self.scen else ZERO

    @property
    def x(self) -> Decimal:
        """Base FX rate on the line's date (every line carries both rates, D46)."""
        return (self.base or self.scen).fx_rate_base  # type: ignore[union-attr,return-value]

    @property
    def y(self) -> Decimal:
        return (self.scen or self.base).fx_rate_scenario  # type: ignore[union-attr,return-value]

    @property
    def delta_native(self) -> Decimal:
        return self.b - self.a

    @property
    def delta_usd(self) -> Decimal:
        """Scenario line's amount_usd_scenario minus base line's amount_usd_base (cents, 4.1)."""
        b = self.scen.amount_usd_scenario if self.scen else ZERO
        a = self.base.amount_usd_base if self.base else ZERO
        return b - a

    @property
    def delta_usd_exact(self) -> Decimal:
        """b*y - a*x without rounding (P13)."""
        return self.b * self.y - self.a * self.x


def pair_lines(base: Sequence[CashLine], scen: Sequence[CashLine]) -> list[Pair]:
    by_key: dict[tuple, list] = {}
    for line in base:
        if line.line_key in by_key:
            raise ValueError(f"duplicate base line key {line.line_key}")
        by_key[line.line_key] = [line, None]
    for line in scen:
        slot = by_key.setdefault(line.line_key, [None, None])
        if slot[1] is not None:
            raise ValueError(f"duplicate scenario line key {line.line_key}")
        slot[1] = line
    keys = sorted(by_key, key=lambda k: (k[2], k[0].value, k[1].value, k[3].value, k[4]))
    return [Pair(k, *by_key[k]) for k in keys]
