"""Freight calculation (spec 6.5): realised rates by volume class, contract resets, leakage in
both directions, volume shock, non-USD invoicing, collection timing and volume lines.

Notation follows 6.5: rho_k(d) = I_k(d) / I_k(anchor) - 1, where I_k is the pass's index path
(flat at I_k(T0) in the base pass; dates before T0 read the T0 level).

Decisions: leaked volume stays at spot until the horizon ends (P39); the transit delay applies in
full to shipments inside the scenario window (P36); time-varying keys are resolved on the day
they act: pricing, clause and leakage keys on the shipment day, gamma on the reset date, DSO,
delay-affected share and non-USD share on the shipment day (P37).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, ProvenanceLabel, Trade
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.contracts.volume_line import VolumeLine
from hlag_engine.generator.calendar import month_end
from hlag_engine.modules.interface import LineBook, ModuleInputError, ModuleRunContext
from hlag_engine.modules.paths import whole_days, PricePath, ShapeSpec, daterange, explicit_path, scenario_path, shape_factor

INDEX_SERIES = {
    Trade.ASIA_EUROPE: "WCI_SHA_RTM",
    Trade.ASIA_AMERICA: "WCI_SHA_LAX",
    Trade.EUROPE_AMERICA: "WCI_RTM_NYC",
    Trade.AFRICA_INTRA: "WCI_COMPOSITE",  # PROXY for Africa & Intraregional (6.4)
}
WCI_UNIT = "USD/FEU"
FX_UNIT = "USD per unit"


def usd_per_teu(usd_per_feu: float) -> float:
    """WCI values are USD/FEU; one FEU is two TEU (6.4)."""
    return usd_per_feu / 2


@dataclass
class Cohort:
    item: ExposureItem
    trade: Trade
    key: str  # e.g. ASIA_EUROPE:2026-11
    share: float
    p0: float
    reset: date
    intact: float = 1.0  # fraction of the cohort still on contract terms
    leaked: float = 0.0  # fraction moved to the spot rate (stays at spot to the horizon end, P39)
    lost: float = 0.0  # fraction lost as volume (lambda_loss)
    down_done: bool = False
    up_done: bool = False
    reset_rate: float | None = None
    events: list[dict] = field(default_factory=list)

    def rate(self, d: date) -> float:
        return self.reset_rate if self.reset_rate is not None and d >= self.reset else self.p0


@dataclass
class TradeBook:
    trade: Trade
    volumes: list[ExposureItem] = field(default_factory=list)
    spot: ExposureItem | None = None
    index: ExposureItem | None = None
    cohorts: list[Cohort] = field(default_factory=list)


def group_exposures(exposures: list[ExposureItem]) -> dict[Trade, TradeBook]:
    books: dict[Trade, TradeBook] = {}

    def book(trade: str) -> TradeBook:
        t = Trade(trade)
        return books.setdefault(t, TradeBook(t))

    for item in exposures:
        key = item.sub_id.split(":", 2)[2]
        trade = key.split(":")[0]
        if item.kind == "volume":
            book(trade).volumes.append(item)
        elif item.kind == "spot":
            book(trade).spot = item
        elif item.kind == "index":
            book(trade).index = item
        elif item.kind == "contract":
            reset = item.dates["reset_date"]
            assert isinstance(reset, date)
            book(trade).cohorts.append(
                Cohort(item, Trade(trade), key, float(item.quantities["share"]),
                       float(item.quantities["rate_usd_per_teu"]), reset)
            )
    for b in books.values():
        if b.spot is None or b.index is None:
            raise ModuleInputError(f"freight exposures for {b.trade.value} need spot and index items")
        b.volumes.sort(key=lambda i: i.dates["month"])
        b.cohorts.sort(key=lambda c: (c.reset, c.key))
    return books


def shipment_days(item: ExposureItem, horizon_start: date, horizon_end: date) -> list[date]:
    month = item.dates["month"]
    assert isinstance(month, date)
    start, end = max(month, horizon_start), min(month_end(month), horizon_end)
    return list(daterange(start, end))


def index_path(ctx: ModuleRunContext, params: ScenarioParams, trade: Trade, spec: ShapeSpec) -> tuple[PricePath, float]:
    """(path of I_k in USD/FEU, I_k(anchor))."""
    series = INDEX_SERIES[trade]
    t0 = ctx.snapshot(series, WCI_UNIT).value
    anchor = ctx.profile.anchor(series)
    points = params.freight.rate_shock_path.get(trade)
    if points:
        path = explicit_path(t0, [(p.date, p.pct) for p in points])
    else:
        path = scenario_path(t0, params.freight.rate_shock_pct.get(trade, 0.0), spec)
    return path, anchor


@dataclass
class FreightResult:
    book: LineBook
    volume_lines: list[VolumeLine]
    revenue_by_class: dict[str, float]
    revenue_by_trade: dict[Trade, float]
    teu_by_trade: dict[Trade, float]
    exposed_two_quarters: float
    leakage_events: list[dict]
    daily_revenue: dict[date, float]


def run_freight(ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem]) -> FreightResult:
    h_start, h_end = ctx.horizon
    spec = ShapeSpec.for_driver(params, params.freight.shape)
    book = LineBook(OwnerSpec.FREIGHT)
    volume_lines: list[VolumeLine] = []
    revenue_by_class: dict[str, float] = defaultdict(float)
    revenue_by_trade: dict[Trade, float] = defaultdict(float)
    teu_by_trade: dict[Trade, float] = defaultdict(float)
    daily_revenue: dict[date, float] = defaultdict(float)
    exposed = 0.0
    exposed_until = ctx.as_of + timedelta(days=182)
    events: list[dict] = []

    for trade, tb in group_exposures(exposures).items():
        path, anchor = index_path(ctx, params, trade, spec)

        def rho(d: date) -> float:
            return path(d) / anchor - 1

        labels = [tb.spot.label, tb.index.label, *(c.item.label for c in tb.cohorts), *(v.label for v in tb.volumes)]
        label = ProvenanceLabel.weakest(ProvenanceLabel.ASSUMPTION, *labels)
        p0_spot = float(tb.spot.quantities["p0_usd_per_teu"])
        p0_index = float(tb.index.quantities["p0_usd_per_teu"])
        s_spot = float(tb.spot.quantities["share"])
        s_index = float(tb.index.quantities["share"])
        currency = Currency(ctx.resolve(f"freight.invoice_currency.{trade.value}"))
        v_shock = params.freight.volume_shock_pct.get(trade, 0.0)
        delay_days = params.freight.transit_delay_days.get(trade, 0)
        fx_t0: float | None = None

        for vol in tb.volumes:
            daily_base = float(vol.quantities["daily_teu"])
            month = vol.dates["month"]
            month_teu = 0.0
            for d in shipment_days(vol, h_start, h_end):
                lag_p = int(ctx.resolve("freight.pricing_lag_days", d))
                lag_i = int(ctx.resolve("freight.clause_lag_days", d))
                spot_rate = p0_spot * (1 + ctx.resolve("freight.beta", d) * rho(d - timedelta(days=lag_p)))
                index_rate = p0_index * (1 + ctx.resolve("freight.kappa", d) * rho(d - timedelta(days=lag_i)))
                volume = daily_base * (1 + v_shock / 100 * shape_factor(spec, d))

                revenue: list[tuple[str, str, float]] = [
                    (tb.spot.sub_id, "spot", volume * s_spot * spot_rate),
                    (tb.index.sub_id, "index", volume * s_index * index_rate),
                ]
                lost_share = 0.0
                for c in tb.cohorts:
                    if c.reset_rate is None and d >= c.reset:
                        gamma = ctx.resolve("freight.gamma", c.reset)
                        c.reset_rate = c.p0 * (1 + gamma * rho(c.reset))  # index on the reset date, no lag
                    cohort_rate = c.rate(d)
                    gap = spot_rate / cohort_rate - 1
                    if not c.down_done and gap < -ctx.resolve("freight.leakage_gap_down", d):
                        moved = ctx.resolve("freight.leakage_share_down", d) * c.intact
                        lost = ctx.resolve("freight.leakage_volume_loss", d) * c.intact
                        c.intact -= moved + lost
                        c.leaked += moved
                        c.lost += lost
                        c.down_done = True
                        c.events.append({"date": d, "direction": "down", "gap": gap, "moved": moved, "lost": lost})
                        events.append({"cohort": c.key, **c.events[-1]})
                    if not c.up_done and gap > ctx.resolve("freight.leakage_gap_up", d):
                        moved = ctx.resolve("freight.leakage_share_up", d) * c.intact
                        c.intact -= moved
                        c.leaked += moved
                        c.up_done = True
                        c.events.append({"date": d, "direction": "up", "gap": gap, "moved": moved, "lost": 0.0})
                        events.append({"cohort": c.key, **c.events[-1]})
                    revenue.append((c.item.sub_id, "contract", volume * c.share * c.intact * cohort_rate))
                    if c.leaked:
                        revenue.append((f"freight:leaked:{c.key}", "leaked", volume * c.share * c.leaked * spot_rate))
                    lost_share += c.share * c.lost

                shipped = volume * (1 - lost_share)
                month_teu += shipped
                teu_by_trade[trade] += shipped

                n = ctx.resolve(f"freight.non_usd_share.{trade.value}", d)
                if n and fx_t0 is None:
                    fx_t0 = ctx.snapshot(f"FX_USD_PER_{currency.value}", FX_UNIT).value
                affected = ctx.resolve("freight.delay_affected_share", d)
                dso = whole_days(ctx.resolve("freight.collection_lag_days", d))  # P64
                delay = delay_days if spec.in_window(d) else 0
                parts = [(":arr", affected, d + timedelta(days=dso + delay)),
                         (":dep", 1 - affected, d + timedelta(days=dso))]

                for sub_id, cls, amount in revenue:
                    revenue_by_class[cls] += amount
                    revenue_by_trade[trade] += amount
                    daily_revenue[d] += amount
                    if d <= exposed_until and (cls != "contract" or _cohort_resets_by(tb, sub_id, exposed_until)):
                        exposed += amount
                    for suffix, share, when in parts:
                        if share <= 0:
                            continue
                        part = amount * share
                        if n:
                            book.add(sub_id + suffix, LineCode.FREIGHT_REVENUE, Currency.USD, when, part * (1 - n), label)
                            book.add(sub_id + suffix, LineCode.FREIGHT_REVENUE, currency, when, part * n / fx_t0, label)
                        else:
                            book.add(sub_id + suffix, LineCode.FREIGHT_REVENUE, Currency.USD, when, part, label)

            assert isinstance(month, date)
            volume_lines.append(VolumeLine(trade=trade, month=month, teu=Decimal(repr(round(month_teu, 6))),
                                           pass_type=ctx.pass_type, label=label))

    return FreightResult(book, volume_lines, dict(revenue_by_class), dict(revenue_by_trade), dict(teu_by_trade),
                         exposed, events, dict(daily_revenue))


def _cohort_resets_by(tb: TradeBook, sub_id: str, until: date) -> bool:
    return any(c.item.sub_id == sub_id and c.reset <= until for c in tb.cohorts)
