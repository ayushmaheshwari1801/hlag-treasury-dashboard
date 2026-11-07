"""Brent fill rule and beta (spec 7.4, D25, D38).

Filled price = last print x (1 + beta x Brent % change since that print). beta = slope of weekly
fuel % changes on weekly Brent % changes over the last 26 weeks, per grade (hub-weighted),
labelled MARKET with its formula; with fewer than 13 weekly changes, beta = the fallback (1.0),
labelled ASSUMPTION.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from hlag_engine.contracts.enums import ProvenanceLabel

LOOKBACK_WEEKS = 26
MIN_WEEKS = 13

Series = Sequence[tuple[date, float]]


@dataclass(frozen=True)
class BetaEstimate:
    value: float
    label: ProvenanceLabel
    formula: str
    observations: int
    window_start: date | None = None  # first and last weekly print of the estimation window
    window_end: date | None = None

    def to_dict(self) -> dict:
        return {"value": self.value, "label": self.label.value, "formula": self.formula,
                "observations": self.observations,
                "window_start": self.window_start.isoformat() if self.window_start else None,
                "window_end": self.window_end.isoformat() if self.window_end else None}

    @classmethod
    def from_dict(cls, d: dict) -> "BetaEstimate":
        return cls(float(d["value"]), ProvenanceLabel(d["label"]), d["formula"], int(d["observations"]),
                   date.fromisoformat(d["window_start"]) if d.get("window_start") else None,
                   date.fromisoformat(d["window_end"]) if d.get("window_end") else None)


def value_on(series: Series, on: date) -> float | None:
    earlier = [v for d, v in sorted(series) if d <= on]
    return earlier[-1] if earlier else None


def hub_weighted(sin: Series, rtm: Series, w_sin: float) -> list[tuple[date, float]]:
    """Hub-weighted grade series on the dates both hubs print."""
    rtm_by_date = dict(rtm)
    return [(d, w_sin * v + (1 - w_sin) * rtm_by_date[d]) for d, v in sorted(sin) if d in rtm_by_date]


def estimate_beta(fuel: Series, brent: Series, fallback: float, as_of: date) -> BetaEstimate:
    prints = [(d, v) for d, v in sorted(fuel) if d <= as_of][-(LOOKBACK_WEEKS + 1):]
    xs, ys = [], []
    for (d0, f0), (d1, f1) in zip(prints, prints[1:]):
        b0, b1 = value_on(brent, d0), value_on(brent, d1)
        if b0 is None or b1 is None or b0 <= 0 or f0 <= 0:
            continue
        xs.append(b1 / b0 - 1)
        ys.append(f1 / f0 - 1)
    n = len(xs)
    window = (prints[0][0], prints[-1][0]) if prints else (None, None)
    fallback_formula = f"fewer than {MIN_WEEKS} weekly changes: beta = {fallback} (ASSUMPTION)"
    if n < MIN_WEEKS:
        return BetaEstimate(fallback, ProvenanceLabel.ASSUMPTION, fallback_formula, n, *window)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return BetaEstimate(fallback, ProvenanceLabel.ASSUMPTION, "Brent did not move: " + fallback_formula, n, *window)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    return BetaEstimate(slope, ProvenanceLabel.MARKET,
                        f"OLS slope of weekly fuel % changes on weekly Brent % changes, last {n} weeks", n, *window)


def brent_fill(last_print: float, brent_at_print: float, brent_t0: float, beta: float) -> float:
    return last_print * (1 + beta * (brent_t0 / brent_at_print - 1))
