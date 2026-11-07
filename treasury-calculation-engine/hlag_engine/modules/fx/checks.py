"""FX sanity checks (spec 9B.12)."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from hlag_engine.contracts.policy_hedge import CAD_RATIO_CAP

# AR p. 216: earnings effect (USD m) of USD per foreign unit -10% (USD +10%), per currency (D11).
PUBLISHED_DOWN_10 = {"EUR": Decimal("4.0"), "CNY": Decimal("-8.5"), "GBP": Decimal("2.4")}
IMPLIED_POSITIONS_USD_M = {"EUR": Decimal("-40"), "CNY": Decimal("85"), "GBP": Decimal("-24")}

# Storage check: plausible USD per unit bands (ASSUMPTION, P69).
PLAUSIBLE_USD_PER_UNIT = {
    "EUR": (0.8, 1.6), "CAD": (0.5, 1.0), "GBP": (1.0, 1.8),
    "CNY": (0.10, 0.20), "INR": (0.008, 0.020), "BRL": (0.10, 0.35),
}


@dataclass(frozen=True)
class SignCheck:
    move: Decimal
    effects: dict[str, Decimal]  # USD m, one decimal
    expected: dict[str, Decimal]

    @property
    def passed(self) -> bool:
        return self.effects == self.expected


def sign_check(rates_t0: dict[str, float], move: Decimal) -> SignCheck:
    """Feed the implied positions through to_usd: native = position / T0 rate; effect = native x rate x move."""
    effects = {}
    for ccy, position in IMPLIED_POSITIONS_USD_M.items():
        rate = Decimal(repr(rates_t0[ccy]))
        native = position / rate
        effect = native * rate * (1 + move) - native * rate
        effects[ccy] = effect.quantize(Decimal("0.1"))
    sign = Decimal(1) if move < 0 else Decimal(-1)
    expected = {c: sign * v * (abs(move) / Decimal("0.1")) for c, v in PUBLISHED_DOWN_10.items()}
    return SignCheck(move, effects, {c: v.quantize(Decimal("0.1")) for c, v in expected.items()})


def cad_sizing(forward_notional: float, cost_13_weeks: float) -> tuple[float, bool]:
    """Disclosed CAD forward notional over 13 weeks of CAD costs, against the 80% cap."""
    share = forward_notional / cost_13_weeks
    return share, share <= CAD_RATIO_CAP


def storage_problems(rows: Iterable[tuple[str, float, str]]) -> list[str]:
    """rows: (series_id, value, unit). Every FX value must be USD per unit and inside its band."""
    problems = []
    for sid, value, unit in rows:
        if not sid.startswith("FX_USD_PER_"):
            continue
        ccy = sid.removeprefix("FX_USD_PER_")
        if " ".join(unit.split()).lower() != "usd per unit":
            problems.append(f"{sid}: unit {unit!r} is not USD per unit")
        low, high = PLAUSIBLE_USD_PER_UNIT.get(ccy, (0, float("inf")))
        if not low <= value <= high:
            problems.append(f"{sid}: {value} is outside the plausible band [{low}, {high}] (units per USD stored?)")
    return problems
