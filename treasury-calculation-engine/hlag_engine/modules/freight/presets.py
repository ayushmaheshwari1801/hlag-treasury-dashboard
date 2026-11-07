"""Freight presets (spec 6.6). History-based presets store explicit numbers when built (D37), so
running them never needs history."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from hlag_engine.contracts.enums import PresetId, ScenarioSource, Shape, Trade
from hlag_engine.contracts.scenario import FreightParams, HistoryReplay, ScenarioParams, WeeklyShock
from hlag_engine.modules.freight.calc import INDEX_SERIES

History = Mapping[str, Sequence[tuple[date, float]]]

RED_SEA = FreightParams(rate_shock_pct={Trade.ASIA_EUROPE: 40.0}, transit_delay_days={Trade.ASIA_EUROPE: 12})
DEMAND_SLUMP = FreightParams(
    rate_shock_pct={t: -20.0 for t in Trade}, volume_shock_pct={t: -5.0 for t in Trade}
)
# Common blocks: Red Sea step for 182 days; demand slump ramp over 8 weeks, persistent.
STATIC_BLOCKS = {PresetId.RED_SEA_REESCALATION: RED_SEA, PresetId.DEMAND_SLUMP: DEMAND_SLUMP}


def value_on(history: Sequence[tuple[date, float]], on: date) -> float:
    """Latest print on or before a date."""
    earlier = [v for d, v in sorted(history) if d <= on]
    if not earlier:
        raise ValueError(f"no history on or before {on}")
    return earlier[-1]


def build_freight_reversal(history: History, as_of: date, start_date: date) -> ScenarioParams:
    """Each route gives back 50% of its rise over the last 26 weeks, over 12 weeks (only if it rose)."""
    shocks: dict[Trade, float] = {}
    for trade, series in INDEX_SERIES.items():
        now = value_on(history[series], as_of)
        then = value_on(history[series], as_of - timedelta(weeks=26))
        if now > then:
            shocks[trade] = round(-0.5 * (now - then) / now * 100, 6)
    return ScenarioParams(
        name="Freight rate reversal", start_date=start_date, duration_days=None, default_shape=Shape.RAMP,
        ramp_or_revert_weeks=12, source=ScenarioSource.PRESET, preset_id=PresetId.FREIGHT_RATE_REVERSAL,
        freight=FreightParams(rate_shock_pct=shocks),
    )


def build_history_replay(history: History, window_start: date, window_end: date, as_of: date) -> ScenarioParams:
    """rate_shock_path = I(start + (t - T0)) / I(start) - 1, weekly, stored as numbers (6.6)."""
    paths: dict[Trade, list[WeeklyShock]] = {}
    for trade, series in INDEX_SERIES.items():
        base = value_on(history[series], window_start)
        points, week = [], 1
        while window_start + timedelta(weeks=week) <= window_end:
            level = value_on(history[series], window_start + timedelta(weeks=week))
            points.append(WeeklyShock(date=as_of + timedelta(weeks=week), pct=round((level / base - 1) * 100, 6)))
            week += 1
        paths[trade] = points
    return ScenarioParams(
        name="History replay", start_date=as_of + timedelta(days=1), source=ScenarioSource.PRESET,
        preset_id=PresetId.HISTORY_REPLAY,
        freight=FreightParams(history_replay=HistoryReplay(start=window_start, end=window_end), rate_shock_path=paths),
    )
