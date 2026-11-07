"""Fixture loading and single-pass runs for module tests (modules are tested directly; the
orchestrator is step 5)."""

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.schema import Profile, profile_from_dict
from hlag_engine.contracts.enums import PassType
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.modules.interface import ModuleInputs, ModuleOutput, ModuleRunContext, ProfileContext, SnapshotEntry
from tests.conftest import complete_raw

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
M = Decimal(1_000_000)


@dataclass
class Fixture:
    raw: dict[str, Any]
    as_of: date
    profile: Profile
    market: dict[str, SnapshotEntry]
    exposures: list[ExposureItem]
    scenario: ScenarioParams
    expected: dict[str, Any]
    history: dict[str, list[tuple[date, float]]] = field(default_factory=dict)


def build_profile(profile_values: dict[str, Any], market: dict[str, SnapshotEntry],
                  anchor: dict[str, float] | None = None) -> Profile:
    raw = complete_raw()
    for key, value in profile_values.items():
        raw["parameters"][key]["value"] = value
        raw["parameters"][key]["status"] = "ok"
    series = raw["anchor_snapshot"]["series"]
    for sid, entry in market.items():  # anchor = T0 snapshot (5.5), unless given
        if sid in series:
            series[sid]["value"] = (anchor or {}).get(sid, entry.value)
            series[sid]["status"] = "ok"
    return profile_from_dict(raw)


def snapshot(raw_market: dict[str, dict]) -> dict[str, SnapshotEntry]:
    return {sid: SnapshotEntry(**v) for sid, v in raw_market.items()}


def load_fixture(name: str, *, profile_values: dict[str, Any] | None = None,
                 anchor: dict[str, float] | None = None, market: dict[str, dict] | None = None) -> Fixture:
    raw = yaml.safe_load((FIXTURES / name).read_text())
    base = {}
    if "freight_fixture" in raw:  # bunker reuses the freight fixture (TEU, timing)
        base = yaml.safe_load((FIXTURES / raw["freight_fixture"]).read_text())
    values = {**base.get("profile_values", {}), **raw.get("profile_values", {}), **(profile_values or {})}
    mkt = snapshot({**base.get("market", {}), **raw.get("market", {}), **(market or {})})
    exposures = [ExposureItem.model_validate(e) for e in base.get("exposures", []) + raw["exposures"]]
    history = {sid: [(d, float(v)) for d, v in points] for sid, points in raw.get("history", {}).items()}
    return Fixture(raw, raw["as_of"], build_profile(values, mkt, anchor), mkt, exposures,
                   ScenarioParams.model_validate(raw["scenario"]), raw.get("expected", {}), history)


def context(fx: Fixture, params: ScenarioParams, pass_type: PassType, adjustments=(),
            profile: Profile | None = None) -> ModuleRunContext:
    resolver = RunContext.for_scenario(profile or fx.profile, fx.as_of, params, tuple(adjustments))
    return ModuleRunContext(ProfileContext(resolver), pass_type, fx.market, fx.history)


def run_pass(module, fx: Fixture, params: ScenarioParams, pass_type: PassType, inputs: ModuleInputs | None = None,
             exposures: list[ExposureItem] | None = None, **kwargs) -> ModuleOutput:
    p = params.zero_shock() if pass_type is PassType.BASE else params
    return module.run(context(fx, params, pass_type, **kwargs), p, exposures if exposures is not None else fx.exposures,
                      inputs or ModuleInputs())


def run_both(module, fx: Fixture, params: ScenarioParams | None = None, **kwargs) -> tuple[ModuleOutput, ModuleOutput]:
    params = params or fx.scenario
    return run_pass(module, fx, params, PassType.BASE, **kwargs), run_pass(module, fx, params, PassType.SCENARIO, **kwargs)


def line_key(line):
    return (line.owner_spec, line.line_code, line.date, line.currency, line.sub_id)


def deltas_by_date(base: ModuleOutput, scen: ModuleOutput, codes=None) -> dict[date, Decimal]:
    """Full outer join on the stable key; native delta per date (USD lines only here)."""
    out: dict[date, Decimal] = defaultdict(Decimal)
    for sign, output in ((-1, base), (1, scen)):
        for line in output.cash_lines:
            if codes is None or line.line_code in codes:
                out[line.date] += sign * line.amount
    return dict(out)


def cumulative(deltas: dict[date, Decimal]) -> dict[date, Decimal]:
    total, out = Decimal(0), {}
    for d in sorted(deltas):
        total += deltas[d]
        out[d] = total
    return out


def cumulative_on(cum: dict[date, Decimal], on: date) -> Decimal:
    earlier = [d for d in cum if d <= on]
    return cum[max(earlier)] if earlier else Decimal(0)


def gross_adverse(cum: dict[date, Decimal]) -> tuple[Decimal, date | None]:
    """Largest cumulative adverse delta and the first date it is reached (4.3)."""
    worst, when = Decimal(0), None
    for d in sorted(cum):
        if -cum[d] > worst:
            worst, when = -cum[d], d
    return worst, when


def usd_m(amount, spec: str) -> bool:
    """True when amount (USD) equals the spec figure written in USD m, at the spec's decimals."""
    places = len(spec.split(".")[1]) if "." in spec else 0
    return round(Decimal(str(amount)) / M, places) == Decimal(spec)
