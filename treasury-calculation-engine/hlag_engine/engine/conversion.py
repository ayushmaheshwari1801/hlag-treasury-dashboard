"""FX conversion layer (spec 9A) as standalone functions; the orchestrator (step 5) calls them.

- fx_paths: base and scenario USD per unit per currency (4.2), built with the shared path helpers.
- convert_lines: every line, from either pass, gets both rates for its date and both converted
  amounts (D46); USD lines take 1.0. Conversion is a multiplication (3.6). A currency with no stored
  value at all raises FxConversionError; a stale value is used (the run becomes INCOMPLETE, 4.6).
- apply_policy_hedges: forwards from the FX module's PolicyHedgeSpecs, sized on each hedged line's
  own native amount (D18), settlement = notional x (S(value date) - K) in USD, owner fx, both passes.
- net_positions / exposure_13_weeks: reporting only; netting never changes USD totals (9A.4).
- translation_memo: CNY and GBP positions x (scenario rate at horizon end / base rate - 1), never on
  the path (D19). display_in_eur: one conversion at the base USD/EUR rate for both paths (D47).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from hlag_engine.contracts.cash_line import CashLine, round_to_cents
from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, PassType
from hlag_engine.contracts.exposure import MissingInput
from hlag_engine.contracts.policy_hedge import PolicyHedgeSpec
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.modules.interface import SnapshotEntry
from hlag_engine.modules.paths import PricePath, ShapeSpec, base_path, scenario_path

ONE = Decimal(1)
RATE_PLACES = Decimal("1e-10")  # stored FX rates: 10 decimal places, so float noise never reaches a line (P70)


class FxConversionError(ValueError):
    """A currency used by a line has no stored FX value at all (9A.2)."""


def series_id(currency: Currency) -> str:
    return f"FX_USD_PER_{currency.value}"


@dataclass(frozen=True)
class FxPaths:
    base: dict[Currency, PricePath]
    scenario: dict[Currency, PricePath]

    def path(self, currency: Currency, pass_type: PassType) -> PricePath:
        return (self.base if pass_type is PassType.BASE else self.scenario)[currency]


def fx_paths(market: Mapping[str, SnapshotEntry], params: ScenarioParams, currencies: Iterable[Currency]) -> FxPaths:
    """fx_paths (4.2): base flat at T0; scenario = T0 x (1 + move_pct x f(t)) with the currency's shape."""
    base, scen = {}, {}
    for ccy in set(currencies) - {Currency.USD}:
        entry = market.get(series_id(ccy))
        if entry is None:
            raise FxConversionError(f"no stored value for {series_id(ccy)}: lines in {ccy.value} cannot be converted")
        spec = ShapeSpec.for_driver(params, params.fx.shape.get(ccy))
        base[ccy] = base_path(entry.value)
        scen[ccy] = scenario_path(entry.value, params.fx.move_pct.get(ccy, 0.0), spec)
    return FxPaths(base, scen)


def rate_on(paths: FxPaths, currency: Currency, d: date, pass_type: PassType) -> Decimal:
    if currency is Currency.USD:
        return ONE
    try:
        return Decimal(repr(paths.path(currency, pass_type)(d))).quantize(RATE_PLACES)
    except KeyError:
        raise FxConversionError(f"no FX path for {currency.value}") from None


def to_usd(amount: Decimal, currency: Currency, d: date, paths: FxPaths, pass_type: PassType) -> tuple[Decimal, Decimal]:
    """(rate, USD amount rounded to cents) for one amount on one date and one path."""
    rate = rate_on(paths, currency, d, pass_type)
    return rate, round_to_cents(amount * rate)


def convert_lines(lines: Sequence[CashLine], paths: FxPaths) -> list[CashLine]:
    """Fill fx_rate_base, fx_rate_scenario, amount_usd_base and amount_usd_scenario on every line (D46)."""
    out = []
    for line in lines:
        x, usd_b = to_usd(line.amount, line.currency, line.date, paths, PassType.BASE)
        y, usd_s = to_usd(line.amount, line.currency, line.date, paths, PassType.SCENARIO)
        out.append(CashLine.model_validate({**line.model_dump(), "fx_rate_base": x, "fx_rate_scenario": y,
                                            "amount_usd_base": usd_b, "amount_usd_scenario": usd_s}))
    return out


# ---------------------------------------------------------------- policy hedges (9A.3)


@dataclass(frozen=True)
class HedgeContract:
    spec: PolicyHedgeSpec
    notional: float  # foreign currency, positive when buying foreign (hedging an outflow)
    value_date: date
    contract_rate: float
    spot_at_value: float
    settlement_usd: float
    k_source: str = "path"  # locked, history (started on or before T0) or path (after T0)
    k_source_date: date | None = None  # date of the history value used

    @property
    def key(self) -> str:
        return f"{self.spec.policy.value}:{self.spec.hedged_sub_id}"


@dataclass
class HedgeResult:
    contracts: list[HedgeContract] = field(default_factory=list)
    settlement_lines: list[CashLine] = field(default_factory=list)
    missing: list[MissingInput] = field(default_factory=list)


def history_value(history: Sequence[tuple[date, float]], on: date) -> float | None:
    found = history_point(history, on)
    return found[1] if found else None


def history_point(history: Sequence[tuple[date, float]], on: date) -> tuple[date, float] | None:
    earlier = [(d, v) for d, v in sorted(history) if d <= on]
    return earlier[-1] if earlier else None


def apply_policy_hedges(specs: Sequence[PolicyHedgeSpec], lines: Sequence[CashLine], paths: FxPaths,
                        pass_type: PassType, as_of: date,
                        history: Mapping[str, Sequence[tuple[date, float]]],
                        stored_k: Mapping[str, Mapping] | None = None) -> HedgeResult:
    """Contracts and HEDGE_SETTLEMENT lines for one pass (lines: that pass's lines after snapping).

    stored_k: contract rates of contracts started on or before T0, keyed "<policy>:<hedged sub_id>", as stored
    in a run record; replay passes them instead of history (P71).
    """
    result = HedgeResult()
    by_key = {(l.line_code, l.currency, l.sub_id): l for l in lines if l.owner_spec is OwnerSpec.FX}
    for spec in specs:
        line = by_key.get((spec.hedged_line_code, spec.currency, spec.hedged_sub_id))
        if line is None:
            continue  # the hedged line is not in this pass (for example dated after the horizon)
        path = paths.path(spec.currency, pass_type)
        key = f"{spec.policy.value}:{spec.hedged_sub_id}"
        source, source_date = "path", None
        if spec.locked_rate is not None:
            k, source = spec.locked_rate, "locked"
        elif spec.start_date <= as_of:
            source = "history"
            if stored_k and key in stored_k:
                k = float(stored_k[key]["rate"])
                source_date = date.fromisoformat(stored_k[key]["source_date"]) if stored_k[key].get("source_date") else None
            else:
                point = history_point(history.get(series_id(spec.currency), ()), spec.start_date)
                k, source_date = (point[1], point[0]) if point else (None, None)
            if k is None and spec.start_date == as_of:
                k, source_date = path(as_of), as_of  # the T0 snapshot value
            if k is None:
                result.missing.append(MissingInput(key=series_id(spec.currency), status="no_history_on_start_date",
                                                   affects=f"policy hedge {spec.policy.value} {spec.hedged_sub_id}"))
                continue
        else:
            k = path(spec.start_date)  # the pass's own path (D45)
        notional = -spec.ratio * float(line.amount)  # buy foreign for an outflow
        spot = path(line.date)
        settlement = notional * (spot - k)
        result.contracts.append(HedgeContract(spec, notional, line.date, k, spot, settlement, source, source_date))
        result.settlement_lines.append(CashLine(
            sub_id=f"fx:hedge:{spec.policy.value}:{spec.hedged_sub_id}", date=line.date, currency=Currency.USD,
            amount=round_to_cents(settlement), owner_spec=OwnerSpec.FX, line_code=LineCode.HEDGE_SETTLEMENT,
            label=spec.label, pass_type=pass_type,
        ))
    return result


# ---------------------------------------------------------------- netting (9A.4), reporting only


@dataclass
class NetPosition:
    gross_outflows: Decimal = Decimal(0)  # native, negative
    gross_inflows: Decimal = Decimal(0)  # native, positive
    hedged: Decimal = Decimal(0)  # hedge notional, native
    net: Decimal = Decimal(0)

    @property
    def unhedged_net(self) -> Decimal:
        return self.net + self.hedged


def net_positions(lines: Iterable[CashLine], contracts: Iterable[HedgeContract] = ()) -> dict[tuple[Currency, date], NetPosition]:
    """Per currency and date: gross outflows, gross inflows, hedged amount and unhedged net (all owners)."""
    out: dict[tuple[Currency, date], NetPosition] = defaultdict(NetPosition)
    for line in lines:
        if line.currency is Currency.USD:
            continue
        pos = out[(line.currency, line.date)]
        if line.amount < 0:
            pos.gross_outflows += line.amount
        else:
            pos.gross_inflows += line.amount
        pos.net += line.amount
    for c in contracts:
        out[(c.spec.currency, c.value_date)].hedged += round_to_cents(c.notional)
    return dict(out)


def exposure_13_weeks(lines: Iterable[CashLine], contracts: Iterable[HedgeContract], as_of: date) -> dict[Currency, NetPosition]:
    """The dashboard's 13-week exposure table: net positions summed per currency up to T0 + 91 days."""
    until = as_of + timedelta(days=91)
    totals: dict[Currency, NetPosition] = defaultdict(NetPosition)
    for (ccy, d), pos in net_positions(lines, contracts).items():
        if d <= until:
            t = totals[ccy]
            t.gross_outflows += pos.gross_outflows
            t.gross_inflows += pos.gross_inflows
            t.hedged += pos.hedged
            t.net += pos.net
    return dict(totals)


def convert_net(positions: Mapping[tuple[Currency, date], NetPosition], paths: FxPaths, pass_type: PassType) -> Decimal:
    """USD value of the netted positions, without per-line rounding (for the netting check)."""
    return sum((pos.net * rate_on(paths, ccy, d, pass_type) for (ccy, d), pos in positions.items()), Decimal(0))


# ---------------------------------------------------------------- translation memo and display (9A.5)


def translation_memo(positions_usd: Mapping[Currency, float], paths: FxPaths, horizon_end: date) -> dict[Currency, float]:
    """position x (scenario rate at horizon end / base rate - 1), USD; never on the path (D19)."""
    return {
        ccy: pos * (paths.scenario[ccy](horizon_end) / paths.base[ccy](horizon_end) - 1)
        for ccy, pos in positions_usd.items()
    }


def display_in_eur(amount_usd: Decimal | float, usd_per_eur_base: float) -> Decimal:
    """Display only: one conversion at the base USD/EUR rate for both paths (D47)."""
    return Decimal(repr(float(amount_usd))) / Decimal(repr(usd_per_eur_base))
