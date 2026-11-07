"""Bunker calculation (spec 7.5): blended price, monthly price and consumption, fuel purchases,
MFR change and its cash timing.

Decisions (P37, P38):
- Hub weights w(t) and scrubber share s(t) are resolved per day. Each hub-and-grade purchase line
  is priced at that series' monthly average; tonnes use the purchase day's weights.
- MFR_m = sigma x (Ref_m - P_base) x fuel per TEU x TEU_m, with Ref_m = P_{m-L} from the pass's
  price path and P_base the blend of the anchor snapshot (H1 2026, D35) over the same days and
  with the same weights as Ref_m. The base pass therefore carries sigma x (P_T0 blend - P_anchor
  blend) x fuel per TEU x TEU; the scenario delta comes on top and P_base cancels in it.
- MFR is collected like freight: shipment day + DSO, plus the trade's transit delay on freight's
  delay-affected share (D16). TEU per day = the month's volume line spread evenly over its days.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.calendar import add_months, month_end
from hlag_engine.modules.bunker.brent import BetaEstimate, brent_fill, estimate_beta, hub_weighted, value_on
from hlag_engine.modules.interface import LineBook, ModuleInputError, ModuleInputs, ModuleRunContext
from hlag_engine.modules.paths import whole_days, PricePath, ShapeSpec, average_shape_factor, daterange, scenario_path

HUBS = ("SIN", "RTM")
GRADES = ("VLSFO", "HSFO")
SERIES = {(h, g): f"{g}_{h}" for h in HUBS for g in GRADES}
FUEL_UNIT = "USD/t"


def month_days(month: date) -> list[date]:
    return list(daterange(month, month_end(month)))


def ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


@dataclass
class Prices:
    """T0 values (after any Brent fill), anchors and the pass's price paths."""

    t0: dict[str, float]
    anchor: dict[str, float]
    paths: dict[str, PricePath]
    proxy_series: list[str] = field(default_factory=list)
    brent: "BrentInputs | None" = None


@dataclass
class BrentInputs:
    """Values derived from history: beta per grade, Brent at print date and T0 for each stale print, and the
    Brent-VLSFO 4-week gap. Stored in the run record and reused on replay (P71)."""

    betas: dict[str, BetaEstimate]
    fill: dict[str, dict]  # series -> {print_date, brent_at_print, brent_t0}
    gap_pp: float | None = None

    def to_dict(self) -> dict:
        return {"brent_beta": {g: b.to_dict() for g, b in self.betas.items()}, "brent_fill": self.fill,
                "brent_vlsfo_gap_pp": self.gap_pp}

    @classmethod
    def from_dict(cls, d: dict) -> "BrentInputs":
        return cls({g: BetaEstimate.from_dict(b) for g, b in d["brent_beta"].items()}, dict(d["brent_fill"]),
                   d.get("brent_vlsfo_gap_pp"))


def brent_inputs(ctx: ModuleRunContext) -> BrentInputs:
    stored = ctx.derived_inputs.get("bunker")
    if stored:
        return BrentInputs.from_dict(stored)
    w_sin = float(ctx.resolve("bunker.hub_weight_sin"))
    brent = ctx.history.get("BRENT", ())
    fallback = float(ctx.resolve("bunker.beta_fallback"))
    betas, fill = {}, {}
    for grade in GRADES:
        fuel = hub_weighted(ctx.history.get(f"{grade}_SIN", ()), ctx.history.get(f"{grade}_RTM", ()), w_sin)
        betas[grade] = estimate_beta(fuel, brent, fallback, ctx.as_of)
    for sid in SERIES.values():
        entry = ctx.market.get(sid)
        if entry is not None and entry.stale:
            fill[sid] = {"print_date": entry.value_date.isoformat(), "brent_at_print": value_on(brent, entry.value_date),
                         "brent_t0": value_on(brent, ctx.as_of)}
    gap = None
    vlsfo = hub_weighted(ctx.history.get("VLSFO_SIN", ()), ctx.history.get("VLSFO_RTM", ()), w_sin)
    if vlsfo and brent:
        then = ctx.as_of - timedelta(weeks=4)
        values = (value_on(vlsfo, ctx.as_of), value_on(vlsfo, then), value_on(brent, ctx.as_of), value_on(brent, then))
        if None not in values:
            f_now, f_then, b_now, b_then = values
            gap = ((f_now / f_then) - (b_now / b_then)) * 100
    return BrentInputs(betas, fill, gap)


def t0_prices(ctx: ModuleRunContext, inputs: BrentInputs) -> tuple[dict[str, float], list[str]]:
    """Last weekly print at or before T0 (D38); a stale print switches to the Brent fill (PROXY)."""
    values, proxy = {}, []
    for (hub, grade), sid in SERIES.items():
        entry = ctx.snapshot(sid, FUEL_UNIT)
        value = entry.value
        if entry.stale:
            fill = inputs.fill.get(sid) or {}
            at_print, at_t0 = fill.get("brent_at_print"), fill.get("brent_t0")
            if at_print is None or at_t0 is None:
                raise ModuleInputError(f"{sid} is stale and Brent history is missing for the fill")
            value = brent_fill(entry.value, at_print, at_t0, inputs.betas[grade].value)
            proxy.append(sid)
        values[sid] = value
    for hub in HUBS:
        if values[f"HSFO_{hub}"] > values[f"VLSFO_{hub}"]:
            raise ModuleInputError(
                f"grade check: HSFO_{hub} ({values[f'HSFO_{hub}']}) is above VLSFO_{hub} "
                f"({values[f'VLSFO_{hub}']}); the grades look swapped"
            )
    return values, proxy


def build_prices(ctx: ModuleRunContext, params: ScenarioParams, spec: ShapeSpec) -> Prices:
    inputs = brent_inputs(ctx)
    t0, proxy = t0_prices(ctx, inputs)
    shocks = {"VLSFO": params.bunker.vlsfo_shock_pct, "HSFO": params.bunker.hsfo_shock_pct}
    paths = {sid: scenario_path(t0[sid], shocks[g], spec) for (h, g), sid in SERIES.items()}
    anchor = {sid: ctx.profile.anchor(sid) for sid in SERIES.values()}
    return Prices(t0, anchor, paths, proxy, inputs)


class Blend:
    """Blended price P(t) = sum_h w_h(t) x ((1 - s(t)) VLSFO_h(t) + s(t) HSFO_h(t)) (7.5)."""

    def __init__(self, ctx: ModuleRunContext):
        self.ctx = ctx
        self._weights = lru_cache(maxsize=None)(self._resolve_weights)

    def _resolve_weights(self, d: date) -> tuple[float, float]:
        return float(self.ctx.resolve("bunker.hub_weight_sin", d)), float(self.ctx.resolve("bunker.scrubber_share", d))

    def weights(self, d: date) -> tuple[float, float]:
        return self._weights(d)

    def blend(self, d: date, price: Callable[[str], float]) -> float:
        w_sin, s = self.weights(d)
        return sum(
            w * ((1 - s) * price(f"VLSFO_{hub}") + s * price(f"HSFO_{hub}"))
            for hub, w in (("SIN", w_sin), ("RTM", 1 - w_sin))
        )

    def price(self, prices: Prices, d: date) -> float:
        return self.blend(d, lambda sid: prices.paths[sid](d))

    def anchor_price(self, prices: Prices, d: date) -> float:
        return self.blend(d, lambda sid: prices.anchor[sid])

    def month_average(self, fn: Callable[[date], float], month: date) -> float:
        days = month_days(month)
        return sum(fn(d) for d in days) / len(days)


@dataclass
class BunkerResult:
    book: LineBook
    monthly_price: dict[date, float]
    monthly_consumption: dict[date, float]
    mfr_by_month: dict[date, float]
    purchases_by_month: dict[date, float]
    prices: Prices
    blend: Blend


def purchase_months(exposures: list[ExposureItem]) -> dict[date, float]:
    """Base tonnes per month inside the horizon (sum of the month's hub and grade items)."""
    months: dict[date, float] = defaultdict(float)
    for item in exposures:
        if item.kind == "purchase":
            month = item.dates["month"]
            assert isinstance(month, date)
            months[month] += float(item.quantities["tonnes"])
    return dict(sorted(months.items()))


def run_bunker(ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem],
               inputs: ModuleInputs) -> BunkerResult:
    h_start, h_end = ctx.horizon
    spec = ShapeSpec.for_driver(params, params.bunker.shape)
    prices = build_prices(ctx, params, spec)
    blend = Blend(ctx)
    book = LineBook(OwnerSpec.BUNKER)
    label = ProvenanceLabel.ASSUMPTION  # hub weights and scrubber share (2.4)
    u, a = params.bunker.consumption_uplift_pct / 100, params.bunker.uplift_affected_share
    monthly_price, monthly_consumption, purchases = {}, {}, {}

    series_avg: dict[tuple[str, date], float] = {}

    def avg(sid: str, month: date) -> float:
        if (sid, month) not in series_avg:
            series_avg[(sid, month)] = blend.month_average(prices.paths[sid], month)
        return series_avg[(sid, month)]

    for month, base_tonnes in purchase_months(exposures).items():
        days = [d for d in month_days(month) if h_start <= d <= h_end]
        consumption = base_tonnes * (1 + u * a * average_shape_factor(spec, days))
        monthly_consumption[month] = consumption
        monthly_price[month] = blend.month_average(lambda d: blend.price(prices, d), month)
        total = 0.0
        for d in days:
            w_sin, s = blend.weights(d)
            pay = d + timedelta(days=int(ctx.resolve("bunker.supplier_lag_days", d)))
            for hub, w in (("SIN", w_sin), ("RTM", 1 - w_sin)):
                for grade, share in (("VLSFO", 1 - s), ("HSFO", s)):
                    if w * share == 0:
                        continue
                    amount = -consumption / len(days) * w * share * avg(SERIES[(hub, grade)], month)
                    total += amount
                    book.add(f"bunker:purchase:{hub}:{grade}:{ym(month)}", LineCode.FUEL_PURCHASE, Currency.USD,
                             pay, amount, label)
        purchases[month] = total

    mfr_by_month = _mfr(ctx, params, inputs, prices, blend, book, label)
    return BunkerResult(book, monthly_price, monthly_consumption, mfr_by_month, purchases, prices, blend)


def _mfr(ctx: ModuleRunContext, params: ScenarioParams, inputs: ModuleInputs, prices: Prices, blend: Blend,
         book: LineBook, label: ProvenanceLabel) -> dict[date, float]:
    h_start, h_end = ctx.horizon
    freight_spec = ShapeSpec.for_driver(params, params.freight.shape)
    fuel_per_teu = float(ctx.resolve("bunker.fuel_per_teu_t"))
    out: dict[date, float] = defaultdict(float)
    for vl in inputs.volume_lines:
        if vl.pass_type is not ctx.pass_type:
            raise ModuleInputError(f"volume line for {vl.trade.value} {vl.month} is from the {vl.pass_type.value} pass")
        days = [d for d in month_days(vl.month) if h_start <= d <= h_end]
        if not days or vl.teu == 0:
            continue
        lag = int(ctx.resolve("bunker.mfr_lag_months", days[0]))
        ref_month = add_months(vl.month, -lag)
        ref = blend.month_average(lambda d: blend.price(prices, d), ref_month)
        p_base = blend.month_average(lambda d: blend.anchor_price(prices, d), ref_month)
        teu_day = float(vl.teu) / len(days)
        delay_days = params.freight.transit_delay_days.get(vl.trade, 0)
        for d in days:
            amount = float(ctx.resolve("bunker.mfr_share", d)) * (ref - p_base) * fuel_per_teu * teu_day
            out[vl.month] += amount
            if amount == 0:
                continue
            affected = float(ctx.resolve("freight.delay_affected_share", d))
            dso = whole_days(ctx.resolve("freight.collection_lag_days", d))  # P64
            delay = delay_days if freight_spec.in_window(d) else 0
            for suffix, share, when in ((":arr", affected, d + timedelta(days=dso + delay)),
                                        (":dep", 1 - affected, d + timedelta(days=dso))):
                if share > 0:
                    book.add(f"bunker:mfr:{vl.trade.value}{suffix}", LineCode.MFR_RECOVERY, Currency.USD, when,
                             amount * share, label)
    return dict(out)
