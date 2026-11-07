"""Attribution (spec 4.3) and gross adverse delta per driver, in exact Decimal on unrounded values (P13).

Driver bar (freight, bunker, rates) = sum over the driver's pairs of (b - a) x.
FX bar = sum over all pairs of b (y - x) + sum over fx-owned pairs of (b - a) x.
Total = sum of (b y - a x); the bars must sum to it within USD 0.01 (P9); in exact Decimal they are equal.
G_d = the largest cumulative adverse delta of the driver's gross lines at any date, at base FX; rates takes
the larger of the debt side and the cash side, each alone; fx takes the conversion effect sum b (y - x)
before policy hedge settlements.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hlag_engine.contracts.enums import LineCode, OwnerSpec
from hlag_engine.engine.pairing import Pair

SUM_TOLERANCE_USD = Decimal("0.01")
DRIVERS = ("freight", "bunker", "rates", "fx")


class AttributionError(ValueError):
    pass


@dataclass(frozen=True)
class Attribution:
    freight: Decimal
    bunker: Decimal
    rates: Decimal
    fx: Decimal
    total: Decimal
    direct: Decimal = Decimal(0)  # direct cash flows (deviation from spec 4.3; decisions_pending.md)

    def bars(self) -> dict[str, Decimal]:
        return {"freight": self.freight, "bunker": self.bunker, "rates": self.rates, "fx": self.fx,
                "direct": self.direct}


def attribute(pairs: Sequence[Pair], up_to: date | None = None) -> Attribution:
    bars = defaultdict(Decimal)
    total = Decimal(0)
    for p in pairs:
        if up_to is not None and p.ref.date > up_to:
            continue
        owner = p.ref.owner_spec
        native = (p.b - p.a) * p.x
        bars["fx" if owner is OwnerSpec.FX else owner.value] += native
        bars["fx"] += p.b * (p.y - p.x)
        total += p.delta_usd_exact
    result = Attribution(bars["freight"], bars["bunker"], bars["rates"], bars["fx"], total, bars["direct"])
    if abs(sum(result.bars().values()) - total) > SUM_TOLERANCE_USD:
        raise AttributionError(f"bars sum to {sum(result.bars().values())}, total is {total}")
    return result


@dataclass(frozen=True)
class GrossAdverse:
    value: Decimal  # positive USD, 0 if never adverse
    date: date | None


def _worst(by_date: dict[date, Decimal]) -> GrossAdverse:
    total, worst, when = Decimal(0), Decimal(0), None
    for d in sorted(by_date):
        total += by_date[d]
        if -total > worst:
            worst, when = -total, d
    return GrossAdverse(worst, when)


def _driver(pairs: Iterable[Pair], owner: OwnerSpec, codes: set[LineCode]) -> GrossAdverse:
    by_date: dict[date, Decimal] = defaultdict(Decimal)
    for p in pairs:
        if p.ref.owner_spec is owner and p.ref.line_code in codes:
            by_date[p.ref.date] += (p.b - p.a) * p.x
    return _worst(by_date)


def gross_adverse(pairs: Sequence[Pair], gross_codes: dict[str, set[LineCode]],
                  rates_sides: tuple[set[LineCode], set[LineCode]]) -> dict[str, GrossAdverse]:
    out = {
        "freight": _driver(pairs, OwnerSpec.FREIGHT, gross_codes.get("freight", set())),
        "bunker": _driver(pairs, OwnerSpec.BUNKER, gross_codes.get("bunker", set())),
    }
    debt, cash = (_driver(pairs, OwnerSpec.RATES, side) for side in rates_sides)
    out["rates"] = max(debt, cash, key=lambda g: g.value)
    conversion: dict[date, Decimal] = defaultdict(Decimal)
    for p in pairs:
        if p.ref.line_code is not LineCode.HEDGE_SETTLEMENT:
            conversion[p.ref.date] += p.b * (p.y - p.x)
    out["fx"] = _worst(conversion)
    return out


def cumulative_delta(pairs: Sequence[Pair]) -> dict[date, Decimal]:
    by_date: dict[date, Decimal] = defaultdict(Decimal)
    for p in pairs:
        by_date[p.ref.date] += p.delta_usd_exact
    total, out = Decimal(0), {}
    for d in sorted(by_date):
        total += by_date[d]
        out[d] = total
    return out
