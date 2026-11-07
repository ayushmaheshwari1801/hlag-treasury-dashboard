"""DriverModule interface (spec 5.1) and the types modules exchange with the engine.

ModuleRunContext is the spec's RunContext (5.1); it is named ModuleRunContext because
hlag_engine.company_profile.resolve.RunContext already names the resolver context (P40).

Rules every module follows (5.2): run reads only its arguments; full gross native amounts in
both passes; one line code per line; shocks only through hlag_engine.modules.paths; both directions; one
provenance label per line; exact calendar dates inside the horizon (later cash goes to the
watch list, never to cash lines); state applied identically in both passes.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from hlag_engine.company_profile.resolve import RunContext as ResolverContext
from hlag_engine.company_profile.resolve import resolve, resolve_detail
from hlag_engine.company_profile.schema import MissingProfileValue
from hlag_engine.contracts.alert_rule import AlertRule
from hlag_engine.contracts.cash_line import CashLine, round_to_cents
from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, PassType, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.contracts.policy_hedge import PolicyHedgeSpec
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.contracts.volume_line import VolumeLine
from hlag_engine.generator.calendar import horizon
from hlag_engine.modules.paths import PricePath, ShapeSpec, base_path, scenario_path, shape_factor


class ModuleInputError(ValueError):
    """Inputs a module cannot use (missing or wrong-unit market data, unsupported hedge test)."""


@dataclass(frozen=True)
class ProfileContext:
    """as_of, horizon dates and resolve(key, date) (5.1)."""

    resolver: ResolverContext

    @property
    def as_of(self) -> date:
        return self.resolver.as_of

    @property
    def horizon(self) -> tuple[date, date]:
        return horizon(self.as_of)

    def resolve(self, key: str, on: date | None = None) -> Any:
        return resolve(key, on or self.as_of, self.resolver)

    def label(self, key: str, on: date | None = None) -> ProvenanceLabel:
        return resolve_detail(key, on or self.as_of, self.resolver).label

    def anchor(self, series_id: str) -> float:
        """Anchor snapshot value (3.7, D70); raises while it is still a placeholder."""
        entry = self.resolver.profile.anchor_snapshot.series.get(series_id)
        if entry is None:
            raise MissingProfileValue(f"anchor_snapshot.{series_id}", "not_in_profile")
        if entry.value is None:
            raise MissingProfileValue(f"anchor_snapshot.{series_id}", entry.status.value)
        return float(entry.value)


@dataclass(frozen=True)
class SnapshotEntry:
    """One series at T0 (4.2)."""

    value: float
    value_date: date
    unit: str
    days_since_print: int = 0
    label: ProvenanceLabel = ProvenanceLabel.MARKET
    confirmed: bool = True
    filled: bool = False
    stale: bool = False  # set by the snapshot builder from the staleness limits (3.6)


@dataclass(frozen=True)
class ModuleRunContext:
    """ProfileContext plus pass type, market snapshot, recent history and the path builders."""

    profile: ProfileContext
    pass_type: PassType
    market: Mapping[str, SnapshotEntry]
    history: Mapping[str, Sequence[tuple[date, float]]] = field(default_factory=dict)
    # Values derived from history in the original run, by module name; on replay modules use these
    # instead of history (P71). Empty in a normal run.
    derived_inputs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def as_of(self) -> date:
        return self.profile.as_of

    @property
    def horizon(self) -> tuple[date, date]:
        return self.profile.horizon

    def resolve(self, key: str, on: date | None = None) -> Any:
        return self.profile.resolve(key, on)

    def snapshot(self, series_id: str, unit: str) -> SnapshotEntry:
        entry = self.market.get(series_id)
        if entry is None:
            raise ModuleInputError(f"no T0 value for {series_id}")
        if " ".join(entry.unit.split()).lower() != unit.lower():
            raise ModuleInputError(f"{series_id} is in {entry.unit!r}, expected {unit!r}")
        if entry.value <= 0:
            raise ModuleInputError(f"{series_id} T0 value {entry.value} is not positive")
        return entry

    # path builders (4.2)
    def base_path(self, t0_value: float) -> PricePath:
        return base_path(t0_value)

    def scenario_path(self, t0_value: float, shock_pct: float, spec: ShapeSpec) -> PricePath:
        return scenario_path(t0_value, shock_pct, spec)

    @staticmethod
    def shape_factor(spec: ShapeSpec, d: date) -> float:
        return shape_factor(spec, d)


@dataclass(frozen=True)
class ModuleInputs:
    """Outputs of upstream modules for the same pass; only bunker uses it (freight volume lines)."""

    volume_lines: tuple[VolumeLine, ...] = ()


@dataclass(frozen=True)
class Metric:
    value: Any
    unit: str
    label: ProvenanceLabel
    formula: str


@dataclass(frozen=True)
class WatchItem:
    """Cash dated after the horizon: reported, never a cash line (5.2)."""

    sub_id: str
    line_code: LineCode
    currency: Currency
    date: date
    amount: float


@dataclass
class ModuleOutput:
    cash_lines: list[CashLine] = field(default_factory=list)
    volume_lines: list[VolumeLine] = field(default_factory=list)
    metrics: dict[str, Metric] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    watch_list: list[WatchItem] = field(default_factory=list)
    policy_hedges: list[PolicyHedgeSpec] = field(default_factory=list)  # FX only (9B.4); the engine passes them to 9A


@dataclass(frozen=True)
class HedgeTarget:
    target: str
    instruments: tuple[str, ...]
    description: str


@runtime_checkable
class DriverModule(Protocol):
    name: str
    line_codes: set[LineCode]
    gross_codes: set[LineCode]
    offset_codes: set[LineCode]
    material_series: list[str]
    required_params: list[str]

    def build_exposures(self, ctx: ProfileContext) -> list[ExposureItem]: ...
    def run(self, ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem],
            inputs: ModuleInputs) -> ModuleOutput: ...
    def alert_rules(self) -> list[AlertRule]: ...
    def playbook(self) -> list[PlaybookEntry]: ...
    def hedge_targets(self) -> list[HedgeTarget]: ...


class LineBook:
    """Collects float amounts per stable key and rounds each key once when lines are built.

    One line per (sub_id, line_code, currency, date); amounts in float64 until stored (2.1, P11).
    """

    def __init__(self, owner: OwnerSpec):
        self.owner = owner
        self.amounts: dict[tuple[str, LineCode, Currency, date], float] = defaultdict(float)
        self.labels: dict[tuple[str, LineCode, Currency, date], ProvenanceLabel] = {}

    def add(self, sub_id: str, code: LineCode, currency: Currency, when: date, amount: float,
            label: ProvenanceLabel) -> None:
        key = (sub_id, code, currency, when)
        self.amounts[key] += amount
        old = self.labels.get(key)
        self.labels[key] = label if old is None else ProvenanceLabel.weakest(old, label)

    def build(self, horizon_end: date) -> tuple[list[CashLine], list[WatchItem]]:
        lines, watch = [], []
        for key in sorted(self.amounts, key=lambda k: (k[3], k[0], k[1].value, k[2].value)):
            sub_id, code, currency, when = key
            amount = self.amounts[key]
            if when > horizon_end:
                watch.append(WatchItem(sub_id, code, currency, when, amount))
                continue
            lines.append(CashLine(sub_id=sub_id, date=when, currency=currency, amount=round_to_cents(amount),
                                  owner_spec=self.owner, line_code=code, label=self.labels[key]))
        return lines, watch
