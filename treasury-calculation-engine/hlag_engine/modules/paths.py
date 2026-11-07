"""Shared price-path helpers and the shape function (spec 2.7, 4.2), used by every module.

Units: shock percents are whole percents (P1); rate steps are basis points; rate levels are
percent. Paths can be read on any date; a scenario shock is zero before its start date, so
dates before T0 read the T0 level (D35).

Shape factor f(t) in [0, 1] (P36):
- f = 0 before start_date, and from start_date + duration_days on (None = persistent).
- step: 1.
- ramp: min(1, days since start / (7 x weeks)).
- spike_revert: max(0, 1 - days since start / (7 x weeks)).
- ramp or spike_revert with no weeks (None or 0) acts as a step.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from hlag_engine.contracts.enums import Shape
from hlag_engine.contracts.scenario import ScenarioParams


@dataclass(frozen=True)
class ShapeSpec:
    shape: Shape
    start_date: date
    duration_days: int | None = None
    weeks: int | None = None

    @classmethod
    def for_driver(cls, params: ScenarioParams, driver_shape: Shape | None) -> "ShapeSpec":
        """A driver's shape defaults to default_shape (D5)."""
        return cls(
            shape=driver_shape or params.default_shape,
            start_date=params.start_date,
            duration_days=params.duration_days,
            weeks=params.ramp_or_revert_weeks,
        )

    def in_window(self, d: date) -> bool:
        if d < self.start_date:
            return False
        return self.duration_days is None or d < self.start_date + timedelta(days=self.duration_days)


def shape_factor(spec: ShapeSpec, d: date) -> float:
    if not spec.in_window(d):
        return 0.0
    if spec.shape is Shape.STEP or not spec.weeks:
        return 1.0
    progress = (d - spec.start_date).days / (7 * spec.weeks)
    if spec.shape is Shape.RAMP:
        return min(1.0, progress)
    return max(0.0, 1.0 - progress)  # spike_revert


def average_shape_factor(spec: ShapeSpec, days: Sequence[date]) -> float:
    return sum(shape_factor(spec, d) for d in days) / len(days) if days else 0.0


@dataclass(frozen=True)
class PricePath:
    """A price or index path; read it with path(d)."""

    t0_value: float
    shock_pct: float = 0.0
    spec: ShapeSpec | None = None
    points: tuple[tuple[date, float], ...] = ()  # explicit (date, pct) points, step-wise (D37, P36)

    def __call__(self, d: date) -> float:
        if self.points:
            pct = 0.0
            for when, value in self.points:
                if when <= d:
                    pct = value
                else:
                    break
            return self.t0_value * (1 + pct / 100)
        if self.spec is None or self.shock_pct == 0:
            return self.t0_value
        return self.t0_value * (1 + self.shock_pct / 100 * shape_factor(self.spec, d))


def base_path(t0_value: float) -> PricePath:
    """The T0 value held flat on every date (no forward curves)."""
    return PricePath(t0_value)


def scenario_path(t0_value: float, shock_pct: float, spec: ShapeSpec) -> PricePath:
    """base x (1 + shock_pct x f(t))."""
    return PricePath(t0_value, shock_pct, spec)


def explicit_path(t0_value: float, points: Sequence[tuple[date, float]]) -> PricePath:
    """An explicit weekly shock path (rate_shock_path); each point holds until the next."""
    ordered = tuple(sorted(points))
    return PricePath(t0_value, points=ordered) if ordered else base_path(t0_value)


@dataclass(frozen=True)
class RatePath:
    """T0 benchmark (percent) plus the cumulative sum of steps (bp) dated on or before each date."""

    t0_value: float
    steps: tuple[tuple[date, float], ...] = ()

    def __call__(self, d: date) -> float:
        return self.t0_value + sum(bp for when, bp in self.steps if when <= d) / 100


def rate_path(t0_value: float, steps: Sequence[tuple[date, float]] = ()) -> RatePath:
    return RatePath(t0_value, tuple(sorted(steps)))


def whole_days(value: float) -> int:
    """A day count to the nearest whole day, halves rounded up (P64: 46.7 -> 47, not truncated)."""
    return int(math.floor(float(value) + 0.5))


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
