"""FX presets (spec 9B.6) and the FX sensitivity family (D50). All values PROPOSED ILLUSTRATIVE."""

from datetime import date, timedelta

from hlag_engine.contracts.enums import Currency, PresetId, ScenarioSource, Shape
from hlag_engine.contracts.scenario import FxParams, ScenarioParams

C = Currency
MOVES = {
    PresetId.EUR_RALLY: ("EUR rally", {C.EUR: 10.0, C.GBP: 5.0, C.CAD: 3.0}),
    PresetId.EM_STRESS: ("Emerging-market stress", {C.INR: -15.0, C.BRL: -15.0}),
    PresetId.BROAD_USD_STRENGTHENING: ("Broad USD strengthening",
                                       {C.EUR: -8.0, C.CAD: -8.0, C.GBP: -8.0, C.CNY: -4.0, C.INR: -10.0, C.BRL: -10.0}),
    PresetId.CNY_DEVALUATION: ("CNY devaluation", {C.CNY: -10.0}),
}
for _ccy in ("EUR", "CAD", "GBP", "CNY", "INR", "BRL"):
    for _dir, _pct in (("up", 10.0), ("down", -10.0)):
        MOVES[PresetId(f"fx_{_ccy.lower()}_{_dir}_10")] = (f"{_ccy} {_pct:+.0f}%", {C(_ccy): _pct})


def build(preset: PresetId, as_of: date) -> ScenarioParams:
    """Step from T0 + 1 day, persistent."""
    name, moves = MOVES[preset]
    return ScenarioParams(name=name, start_date=as_of + timedelta(days=1), duration_days=None,
                          default_shape=Shape.STEP, source=ScenarioSource.PRESET, preset_id=preset,
                          fx=FxParams(move_pct=moves))
