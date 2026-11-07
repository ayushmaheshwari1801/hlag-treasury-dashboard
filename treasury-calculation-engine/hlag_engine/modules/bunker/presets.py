"""Bunker presets (spec 7.6). All values PROPOSED ILLUSTRATIVE, ASSUMPTION. The reversal preset is
computed from history when built and stored as numbers (D37)."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from hlag_engine.contracts.enums import PresetId, ScenarioSource, Shape
from hlag_engine.contracts.scenario import BunkerParams, ScenarioParams
from hlag_engine.modules.bunker.brent import hub_weighted
from hlag_engine.modules.freight.presets import value_on

RED_SEA = BunkerParams(vlsfo_shock_pct=15.0, hsfo_shock_pct=12.0, consumption_uplift_pct=30.0,
                       uplift_affected_share=0.35)  # alpha, Asia-Europe share of consumption
OIL_SPIKE = BunkerParams(vlsfo_shock_pct=30.0, hsfo_shock_pct=20.0)  # spike_revert over 26 weeks
DEMAND_SLUMP = BunkerParams(vlsfo_shock_pct=-15.0, hsfo_shock_pct=-15.0, consumption_uplift_pct=-3.0,
                            uplift_affected_share=1.0)  # ramp over 8 weeks, persistent
STATIC_BLOCKS = {PresetId.RED_SEA_REESCALATION: RED_SEA, PresetId.OIL_SPIKE: OIL_SPIKE,
                 PresetId.DEMAND_SLUMP: DEMAND_SLUMP}


def build_bunker_reversal(history: Mapping[str, Sequence[tuple[date, float]]], as_of: date, start_date: date,
                          hub_weight_sin: float) -> ScenarioParams:
    """Each grade (hub-weighted, P41) gives back 50% of its rise over the last 26 weeks, over 12 weeks."""
    shocks = {}
    for grade in ("VLSFO", "HSFO"):
        series = hub_weighted(history[f"{grade}_SIN"], history[f"{grade}_RTM"], hub_weight_sin)
        now, then = value_on(series, as_of), value_on(series, as_of - timedelta(weeks=26))
        shocks[grade] = round(-0.5 * (now - then) / now * 100, 6) if now > then else 0.0
    return ScenarioParams(
        name="Bunker price reversal", start_date=start_date, duration_days=None, default_shape=Shape.RAMP,
        ramp_or_revert_weeks=12, source=ScenarioSource.PRESET, preset_id=PresetId.BUNKER_PRICE_REVERSAL,
        bunker=BunkerParams(vlsfo_shock_pct=shocks["VLSFO"], hsfo_shock_pct=shocks["HSFO"]),
    )
