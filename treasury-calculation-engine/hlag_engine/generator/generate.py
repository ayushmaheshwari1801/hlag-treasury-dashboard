"""generate(profile, as_of) -> ExposureRegister (spec 3.5).

Deterministic: no random numbers. The same profile, adjustments and as-of date always give
the same register (D26). Static only: never applies shocks. Placeholders that a recipe part
needs are recorded in register.missing and that part is skipped.
"""

from datetime import date

from hlag_engine.contracts.exposure import ExposureRegister
from hlag_engine.generator.calendar import horizon
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.maturities import MaturitySchedule, build_maturity_schedule
from hlag_engine.generator.recipes import bunker_items, freight_items, fx_items, rates_items
from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.schema import Profile


def generate(profile: Profile, as_of: date, ctx: RunContext | None = None,
             cash_at_t0: float | None = None) -> ExposureRegister:
    """cash_at_t0: rolled-forward cash for the rates deposits (P77); None uses the last disclosed cash."""
    ctx = ctx or RunContext(profile=profile, as_of=as_of)
    if ctx.profile is not profile or ctx.as_of != as_of:
        raise ValueError("ctx must use the same profile and as_of")
    start, end = horizon(as_of)
    inputs = Inputs(ctx)

    schedules: list[MaturitySchedule] = inputs.attempt(
        "debt maturity schedule (rates and fx maturities)", lambda: [build_maturity_schedule(inputs, end)]
    )
    schedule = schedules[0] if schedules else None

    items = (
        freight_items(inputs, start, end)
        + bunker_items(inputs, start, end)
        + rates_items(inputs, start, end, schedule, cash_at_t0)
        + fx_items(inputs, start, end, schedule)
    )
    return ExposureRegister(
        as_of=as_of,
        horizon_start=start,
        horizon_end=end,
        profile_version=profile.profile_version,
        profile_hash=profile.content_hash,
        items=items,
        missing=inputs.missing,
    )
