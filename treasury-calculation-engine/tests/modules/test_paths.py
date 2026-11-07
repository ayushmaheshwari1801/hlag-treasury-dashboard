"""Shared path helpers and shape function (2.7, 4.2, P36)."""

from datetime import date, timedelta

import pytest

from hlag_engine.contracts.enums import Shape
from hlag_engine.modules.paths import (
    ShapeSpec,
    average_shape_factor,
    base_path,
    explicit_path,
    rate_path,
    scenario_path,
    shape_factor,
)

START = date(2026, 11, 1)


def f(shape, d, weeks=4, duration=None):
    return shape_factor(ShapeSpec(shape, START, duration, weeks), d)


def test_step():
    assert f(Shape.STEP, START - timedelta(days=1)) == 0
    assert f(Shape.STEP, START) == 1 and f(Shape.STEP, date(2027, 6, 1)) == 1


def test_ramp_and_spike_revert():
    assert f(Shape.RAMP, START) == 0
    assert f(Shape.RAMP, START + timedelta(days=14)) == pytest.approx(0.5)
    assert f(Shape.RAMP, START + timedelta(days=40)) == 1
    assert f(Shape.SPIKE_REVERT, START) == 1
    assert f(Shape.SPIKE_REVERT, START + timedelta(days=7)) == pytest.approx(0.75)
    assert f(Shape.SPIKE_REVERT, START + timedelta(days=60)) == 0


def test_no_weeks_acts_as_step():
    assert f(Shape.RAMP, START, weeks=None) == 1 and f(Shape.SPIKE_REVERT, START + timedelta(days=99), weeks=0) == 1


def test_duration_returns_to_zero():
    assert f(Shape.STEP, START + timedelta(days=181), duration=182) == 1
    assert f(Shape.STEP, START + timedelta(days=182), duration=182) == 0


def test_average_shape_factor():
    spec = ShapeSpec(Shape.STEP, date(2026, 11, 16))
    days = [date(2026, 11, d) for d in range(1, 31)]
    assert average_shape_factor(spec, days) == pytest.approx(0.5)


def test_paths():
    spec = ShapeSpec(Shape.STEP, START)
    assert base_path(100.0)(date(2030, 1, 1)) == 100.0
    p = scenario_path(100.0, 40, spec)
    assert p(START - timedelta(days=30)) == 100.0  # before T0/start: T0 level
    assert p(START) == pytest.approx(140.0)
    down = scenario_path(100.0, -40, spec)
    assert down(START) == pytest.approx(60.0)


def test_explicit_path_is_stepwise():
    p = explicit_path(100.0, [(date(2026, 11, 14), -5.0), (date(2026, 11, 7), 10.0)])
    assert p(date(2026, 11, 6)) == 100.0
    assert p(date(2026, 11, 10)) == pytest.approx(110.0)
    assert p(date(2026, 11, 20)) == pytest.approx(95.0)


def test_rate_path_cumulative():
    r = rate_path(4.0, [(date(2026, 12, 10), -25), (date(2027, 1, 28), -25)])
    assert r(date(2026, 12, 9)) == 4.0
    assert r(date(2026, 12, 10)) == pytest.approx(3.75)
    assert r(date(2027, 2, 1)) == pytest.approx(3.5)


def test_whole_days_rounds_to_nearest():
    from hlag_engine.modules.paths import whole_days

    assert [whole_days(x) for x in (46.7, 46.5, 46.4, 49, 49.0)] == [47, 47, 46, 49, 49]
