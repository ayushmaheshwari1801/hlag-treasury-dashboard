"""Rates presets (spec 8.6). Steps sit on the next central bank meeting dates from the profile; while
those dates are placeholders, a builder returns the missing inputs instead of a scenario."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from hlag_engine.company_profile.schema import MissingProfileValue
from hlag_engine.contracts.enums import Currency, PresetId, RefinancingMode, ScenarioSource
from hlag_engine.contracts.exposure import MissingInput
from hlag_engine.contracts.scenario import RateStep, RatesParams, ScenarioParams
from hlag_engine.modules.interface import ProfileContext

MEETINGS = {Currency.USD: "rates.fomc_meeting_dates", Currency.EUR: "rates.ecb_meeting_dates"}
NAMES = {
    PresetId.FED_CUTTING_CYCLE: "Fed cutting cycle", PresetId.FED_CUTS_ECB_HOLDS: "Fed cuts, ECB holds",
    PresetId.BOTH_HIKING: "Both hiking", PresetId.SPREAD_WIDENING: "Spread widening",
    PresetId.CREDIT_SQUEEZE: "Credit squeeze", PresetId.RATES_UP_100BP: "Rates +100 bp",
    PresetId.RATES_DOWN_100BP: "Rates -100 bp",
}


@dataclass
class PresetBuild:
    params: ScenarioParams | None
    missing: list[MissingInput] = field(default_factory=list)


def next_meetings(ctx: ProfileContext, currency: Currency, count: int) -> list[date]:
    raw = ctx.resolve(MEETINGS[currency])
    dates = sorted(d if isinstance(d, date) else date.fromisoformat(str(d)) for d in raw)
    upcoming = [d for d in dates if d > ctx.as_of][:count]
    if len(upcoming) < count:
        raise MissingProfileValue(MEETINGS[currency], f"only {len(upcoming)} meetings after T0, {count} needed")
    return upcoming


def _params(ctx: ProfileContext, preset: PresetId, rates: RatesParams) -> ScenarioParams:
    return ScenarioParams(name=NAMES[preset], start_date=ctx.as_of + timedelta(days=1), source=ScenarioSource.PRESET,
                          preset_id=preset, rates=rates)


def build(ctx: ProfileContext, preset: PresetId) -> PresetBuild:
    try:
        if preset is PresetId.FED_CUTTING_CYCLE:
            steps = {Currency.USD: [RateStep(date=d, step_bp=-25) for d in next_meetings(ctx, Currency.USD, 4)]}
            return PresetBuild(_params(ctx, preset, RatesParams(rate_path=steps)))
        if preset is PresetId.FED_CUTS_ECB_HOLDS:
            steps = {Currency.USD: [RateStep(date=d, step_bp=-25) for d in next_meetings(ctx, Currency.USD, 2)]}
            return PresetBuild(_params(ctx, preset, RatesParams(rate_path=steps)))
        if preset is PresetId.BOTH_HIKING:
            steps = {c: [RateStep(date=d, step_bp=25) for d in next_meetings(ctx, c, 3)] for c in MEETINGS}
            return PresetBuild(_params(ctx, preset, RatesParams(rate_path=steps)))
        if preset is PresetId.SPREAD_WIDENING:
            return PresetBuild(_params(ctx, preset, RatesParams(credit_spread_change_bp=150)))
        if preset is PresetId.CREDIT_SQUEEZE:
            return PresetBuild(_params(ctx, preset, RatesParams(credit_spread_change_bp=200,
                                                                refinancing_mode=RefinancingMode.UNAVAILABLE)))
        if preset in (PresetId.RATES_UP_100BP, PresetId.RATES_DOWN_100BP):
            bp = 100 if preset is PresetId.RATES_UP_100BP else -100
            steps = {c: [RateStep(date=ctx.as_of, step_bp=bp)] for c in (Currency.USD, Currency.EUR)}
            return PresetBuild(_params(ctx, preset, RatesParams(rate_path=steps)))
    except MissingProfileValue as exc:
        return PresetBuild(None, [MissingInput(key=exc.key, status=exc.status, affects=f"preset {preset.value}")])
    raise ValueError(f"{preset.value} is not a rates preset")
