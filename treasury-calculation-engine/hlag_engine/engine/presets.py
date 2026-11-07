"""The joint preset library (spec 2.7, D6, D62), assembled from the module preset blocks and builders (P85).

Each preset has a permanent ID, a name, a scope, a description and a rationale, and builds its ScenarioParams for a
given T0. Presets that need data the profile does not have yet (for example meeting dates) return the missing
inputs instead of a scenario. presets.json (1.5) can be written from this library once the team fixes the bodies.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from hlag_engine.contracts.enums import PresetId, ScenarioSource, Shape
from hlag_engine.contracts.exposure import MissingInput
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.modules.bunker import presets as bunker_presets
from hlag_engine.modules.freight import presets as freight_presets
from hlag_engine.modules.fx import presets as fx_presets
from hlag_engine.modules.interface import ProfileContext
from hlag_engine.modules.rates import presets as rates_presets


@dataclass(frozen=True)
class Preset:
    preset_id: PresetId
    name: str
    description: str
    rationale: str
    build: Callable[[ProfileContext], rates_presets.PresetBuild]


def _joint(preset_id: PresetId, name: str, shape: Shape, weeks: int | None, duration: int | None, **blocks):
    def build(ctx: ProfileContext) -> rates_presets.PresetBuild:
        return rates_presets.PresetBuild(ScenarioParams(
            name=name, start_date=ctx.as_of + timedelta(days=1), duration_days=duration, default_shape=shape,
            ramp_or_revert_weeks=weeks, source=ScenarioSource.PRESET, preset_id=preset_id, **blocks))
    return build


def _rates(preset_id: PresetId):
    return lambda ctx: rates_presets.build(ctx, preset_id)


def _fx(preset_id: PresetId):
    return lambda ctx: rates_presets.PresetBuild(fx_presets.build(preset_id, ctx.as_of))


P = PresetId
LIBRARY: dict[PresetId, Preset] = {p.preset_id: p for p in [
    Preset(P.RED_SEA_REESCALATION, "Red Sea re-escalation",
           "Asia-Europe rate +40%, transit +12 days; VLSFO +15%, HSFO +12%, +30% burn on 35% of consumption; 182 days",
           "Cape routing: higher rates, slower collections, higher burn (6.6, 7.6, D36)",
           _joint(P.RED_SEA_REESCALATION, "Red Sea re-escalation", Shape.STEP, None, 182,
                  freight=freight_presets.RED_SEA, bunker=bunker_presets.RED_SEA)),
    Preset(P.DEMAND_SLUMP, "Demand slump",
           "All trades rate -20%, volume -5%; VLSFO and HSFO -15%, consumption -3%; ramp over 8 weeks; persistent",
           "Cyclical downturn (6.6, 7.6)",
           _joint(P.DEMAND_SLUMP, "Demand slump", Shape.RAMP, 8, None,
                  freight=freight_presets.DEMAND_SLUMP, bunker=bunker_presets.DEMAND_SLUMP)),
    Preset(P.OIL_SPIKE, "Oil spike", "VLSFO +30%, HSFO +20%, reverting over 26 weeks",
           "VLSFO usually rises more than HSFO, widening the spread (7.6)",
           _joint(P.OIL_SPIKE, "Oil spike", Shape.SPIKE_REVERT, 26, None, bunker=bunker_presets.OIL_SPIKE)),
    Preset(P.FED_CUTTING_CYCLE, "Fed cutting cycle", "USD -25 bp at each of the next four FOMC meetings",
           "Falling USD rates: debt cost falls, deposit income falls (8.6)", _rates(P.FED_CUTTING_CYCLE)),
    Preset(P.FED_CUTS_ECB_HOLDS, "Fed cuts, ECB holds", "USD -25 bp at the next two FOMC meetings; EUR flat",
           "Policy divergence (8.6)", _rates(P.FED_CUTS_ECB_HOLDS)),
    Preset(P.BOTH_HIKING, "Both hiking", "USD and EUR +25 bp at each of the next three meetings",
           "Synchronised tightening (8.6)", _rates(P.BOTH_HIKING)),
    Preset(P.SPREAD_WIDENING, "Spread widening", "Credit spread +150 bp from T0; rollover",
           "Refinancing cost shock (8.6)", _rates(P.SPREAD_WIDENING)),
    Preset(P.CREDIT_SQUEEZE, "Credit squeeze", "Credit spread +200 bp; refinancing unavailable",
           "Maturities paid from cash (8.6)", _rates(P.CREDIT_SQUEEZE)),
    Preset(P.EUR_RALLY, "EUR rally", "EUR +10%, GBP +5%, CAD +3%; step; persistent",
           "Broad USD weakness led by EUR (9B.6)", _fx(P.EUR_RALLY)),
    Preset(P.EM_STRESS, "Emerging-market stress", "INR -15%, BRL -15%; step; persistent",
           "Emerging-market currency stress (9B.6)", _fx(P.EM_STRESS)),
    Preset(P.BROAD_USD_STRENGTHENING, "Broad USD strengthening", "EUR, CAD, GBP -8%; CNY -4%; INR, BRL -10%",
           "Broad USD strength (9B.6)", _fx(P.BROAD_USD_STRENGTHENING)),
    Preset(P.CNY_DEVALUATION, "CNY devaluation", "CNY -10%; step", "CNY policy move (9B.6)", _fx(P.CNY_DEVALUATION)),
    Preset(P.RATES_UP_100BP, "Rates +100 bp", "USD and EUR +100 bp step at T0", "Sensitivity family (D50)",
           _rates(P.RATES_UP_100BP)),
    Preset(P.RATES_DOWN_100BP, "Rates -100 bp", "USD and EUR -100 bp step at T0", "Sensitivity family (D50)",
           _rates(P.RATES_DOWN_100BP)),
    *[Preset(pid, fx_presets.MOVES[pid][0], f"{fx_presets.MOVES[pid][0]} on USD per unit", "Sensitivity family (D50)",
             _fx(pid)) for pid in fx_presets.MOVES if pid.value.startswith("fx_")],
]}

SENSITIVITY = [P.RATES_UP_100BP, P.RATES_DOWN_100BP,
               *[P(f"fx_{c}_{d}_10") for c in ("eur", "cad", "gbp", "cny", "inr", "brl") for d in ("up", "down")]]
STANDARD_SET = [P.RED_SEA_REESCALATION, P.DEMAND_SLUMP, P.OIL_SPIKE, P.FED_CUTTING_CYCLE, P.EUR_RALLY, P.EM_STRESS,
                *SENSITIVITY]


def build_preset(preset_id: PresetId, ctx: ProfileContext, source: ScenarioSource = ScenarioSource.PRESET
                 ) -> tuple[ScenarioParams | None, list[MissingInput]]:
    built = LIBRARY[preset_id].build(ctx)
    if built.params is None:
        return None, built.missing
    return built.params.model_copy(update={"source": source}), []


__all__ = ["LIBRARY", "STANDARD_SET", "SENSITIVITY", "Preset", "build_preset"]
