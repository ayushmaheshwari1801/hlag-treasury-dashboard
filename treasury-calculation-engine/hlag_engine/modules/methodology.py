"""Descriptive methodology data for the four driver modules (read-only presentation layer).

Each module's methodology.py fills a MethodologySpec: plain-language sentences, the main formulas with every symbol
tied to a profile key, market series or anchor value, a rough approximation clearly labelled as such, the
breakdown components and what each assumption is used for. Nothing here is read by run_scenario; the engine's
calculations do not depend on it. hlag_engine.engine.methodology fills in current values and the run breakdown.
"""

from dataclasses import dataclass, field
from enum import StrEnum

APPROXIMATION_LABEL = "rough approximation for intuition"


class SymbolRef(StrEnum):
    """Where a symbol's current value comes from."""

    PARAM = "param"  # profile key, resolved at T0 through the three layers (key may contain {trade}, {ccy})
    SERIES = "series"  # market snapshot value at T0 (key may contain {index}, {fuel}, {ccy})
    ANCHOR = "anchor"  # anchor snapshot value (H1 2026 average)
    SCENARIO = "scenario"  # a scenario parameter, e.g. bunker.consumption_uplift_pct
    NONE = "none"  # a variable with no single current value (a date, a path)


@dataclass(frozen=True)
class Symbol:
    symbol: str
    meaning: str
    ref: SymbolRef = SymbolRef.NONE
    key: str | None = None


@dataclass(frozen=True)
class Formula:
    name: str
    expression: str
    symbols: tuple[Symbol, ...]


@dataclass(frozen=True)
class Component:
    """One part of a row's total. The run breakdown (engine.methodology.breakdown) assigns every paired line to
    exactly one component, so the components add up to the row."""

    component_id: str
    label: str
    description: str
    detail: str | None = None  # what the per-component detail is: "trade", "currency", "module", "line type", ...


@dataclass(frozen=True)
class MethodologySpec:
    driver: str
    title: str
    summary: tuple[str, ...]  # 3-5 plain-language sentences
    formulas: tuple[Formula, ...]
    approximation: Formula  # always shown with APPROXIMATION_LABEL
    components: tuple[Component, ...]
    used_for: dict[str, tuple[str, str]]  # profile key -> (name, what it is used for)
    market_series: dict[str, str]  # series_id -> role in this module
    limitations: tuple[str, ...] = field(default=())  # in addition to the module's NOTES
