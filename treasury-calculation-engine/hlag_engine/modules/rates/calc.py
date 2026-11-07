"""Interest rate calculation (spec 8.5): floating tranches, deposits, fixed debt, the EUR bond, the
lease line, maturities with rollover or unavailable refinancing, the ZIM tranche, and the
positions behind the metrics.

Decisions (P51-P55):
- A floating or deposit period runs [r_n, r_{n+1}); its rate is fixed at r_n and paid at
  r_{n+1}, actual days / 360. The period running at T0 started one reset interval before the first
  reset in the horizon and uses the T0 snapshot benchmark, so a step dated on T0 does not change it.
- Floating rate = max(b(r), 0) + spread; a negative benchmark is a binding floor and is flagged.
  Deposit yield = max(max(b(roll), 0) - spread_below, 0).
- Each maturity slice is split into floating and fixed parts with the D44 shares; the EUR 300m
  bullet bond is not in the slices (P49), so the EUR share is all floating and the fixed part comes
  from the USD fixed debt. Interest stops on the maturity date: floating interest accrues on the
  outstanding notional day by day; USD fixed interest is rate / 12 x the month's day-weighted
  average notional; the bond pays its full coupon until its maturity date.
- Rollover: a USD drawdown per share on the maturity date (the EUR share's at the T0 rate, a state
  value identical in both passes, P23). The new debt pays max(SOFR, 0) + new-issue spread, reset
  quarterly from drawdown; the spread is fixed at drawdown: BB_OAS(T0) + premium on that date +
  credit_spread_change_bp x the scenario shape factor on that date.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, ProvenanceLabel, RefinancingMode
from hlag_engine.contracts.exposure import ExposureItem, MissingInput
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.calendar import add_months, month_end
from hlag_engine.modules.interface import LineBook, ModuleRunContext
from hlag_engine.modules.paths import RatePath, ShapeSpec, daterange, rate_path, shape_factor

BENCHMARKS = {Currency.USD: "SOFR", Currency.EUR: "ESTR"}
PERCENT = "%"
FX_UNIT = "USD per unit"
A = ProvenanceLabel.ASSUMPTION


class MissingMarketInput(Exception):
    def __init__(self, series: str):
        self.series = series
        super().__init__(f"no T0 value for {series}")


def ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def step_dates(start: date, months: int, until: date) -> list[date]:
    """start, start + n months, ... up to `until`; month-end starts stay on month ends."""
    out, k, is_end = [], 0, start == month_end(start)
    while True:
        d = add_months(start, months * k)
        d = month_end(d) if is_end else d
        if d > until:
            return out
        out.append(d)
        k += 1


@dataclass
class Notional:
    """A notional that falls on given dates (maturities); outstanding(d) excludes amounts maturing on or before d."""

    base: float
    reductions: list[tuple[date, float]] = field(default_factory=list)

    def outstanding(self, d: date) -> float:
        return self.base - sum(a for when, a in self.reductions if when <= d)


@dataclass
class Market:
    ctx: ModuleRunContext
    params: ScenarioParams

    def value(self, series: str, unit: str = PERCENT) -> float:
        if series not in self.ctx.market:
            raise MissingMarketInput(series)
        entry = self.ctx.market[series]
        if " ".join(entry.unit.split()).lower() != unit.lower():
            from hlag_engine.modules.interface import ModuleInputError

            raise ModuleInputError(f"{series} is in {entry.unit!r}, expected {unit!r}")
        return entry.value

    def path(self, currency: Currency) -> RatePath:
        steps = [(s.date, s.step_bp) for s in self.params.rates.rate_path.get(currency, [])]
        return rate_path(self.value(BENCHMARKS[currency]), steps)

    def fixing(self, currency: Currency, on: date) -> float:
        """Benchmark used for a rate fixed on a date: the T0 snapshot up to T0, the pass's path after."""
        return self.value(BENCHMARKS[currency]) if on <= self.ctx.as_of else self.path(currency)(on)

    def fx_t0(self) -> float:
        return self.value("FX_USD_PER_EUR", FX_UNIT)


@dataclass
class RatesState:
    book: LineBook
    missing: list[MissingInput] = field(default_factory=list)
    floors: list[dict] = field(default_factory=list)
    float_notional: dict[str, Notional] = field(default_factory=dict)  # sub_id -> native notional
    float_currency: dict[str, Currency] = field(default_factory=dict)
    float_resets: dict[str, list[date]] = field(default_factory=dict)  # incl. the reset before T0
    deposit_balance: dict[str, float] = field(default_factory=dict)  # sub_id -> native balance
    deposit_balance_usd: dict[str, Notional] = field(default_factory=dict)  # falls by the ZIM cash part (P63)
    zim_cash_part: tuple[date, float] | None = None  # (draw date, consideration - drawdown)
    deposit_currency: dict[str, Currency] = field(default_factory=dict)
    deposit_rolls: dict[str, list[date]] = field(default_factory=dict)
    fixed_usd: Notional | None = None
    refi: list[tuple[date, float]] = field(default_factory=list)  # (drawdown date, USD notional)
    maturities: list[dict] = field(default_factory=list)
    zim: tuple[date, float] | None = None
    lease_monthly: float = 0.0
    interest_by_month: dict[date, dict[str, float]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))

    def attempt(self, affects: str, fn: Callable[[], None]) -> None:
        from hlag_engine.company_profile.schema import MissingProfileValue

        try:
            fn()
        except MissingProfileValue as exc:
            self.missing.append(MissingInput(key=exc.key, status=exc.status, affects=affects))
        except MissingMarketInput as exc:
            self.missing.append(MissingInput(key=exc.series, status="missing_market_data", affects=affects))


def items(exposures: list[ExposureItem], owner: str, kind: str) -> list[ExposureItem]:
    return [e for e in exposures if e.owner_spec.value == owner and e.kind == kind]


def periods(resets: list[date], months: int) -> list[tuple[date, date]]:
    """[(start, end)] from the reset before T0 through the period starting at the last reset."""
    edges = [add_months(resets[0], -months), *resets, add_months(resets[-1], months)]
    return list(zip(edges, edges[1:]))


def accrue(notional: Notional, start: date, end: date, rate_pct: float) -> float:
    return sum(notional.outstanding(d) for d in daterange(start, end - timedelta(days=1))) * rate_pct / 100 / 360


class RatesCalc:
    def __init__(self, ctx: ModuleRunContext, params: ScenarioParams, exposures: list[ExposureItem]):
        self.ctx, self.params, self.exposures = ctx, params, exposures
        self.market = Market(ctx, params)
        self.state = RatesState(LineBook(OwnerSpec.RATES))
        self.spec = ShapeSpec.for_driver(params, None)

    # ---------------------------------------------------------------- lines
    def emit(self, sub_id: str, code: LineCode, currency: Currency, when: date, amount: float,
             label: ProvenanceLabel = A, kind: str | None = None) -> None:
        self.state.book.add(sub_id, code, currency, when, amount, label)
        if kind:
            bucket = self.state.interest_by_month[month_end(when)]
            bucket[f"{kind}:{currency.value}"] += amount

    def run(self) -> RatesState:
        s = self.state
        s.attempt("maturity splits", self._maturities)
        s.attempt("floating tranches", self._floating)
        s.attempt("ZIM cash part", self._zim_cash_part)
        s.attempt("deposits", self._deposits)
        s.attempt("fixed debt", self._fixed)
        s.attempt("lease interest", self._lease)
        s.attempt("refinancing", self._refinancing)
        s.attempt("ZIM tranche", self._zim)
        return s

    # ---------------------------------------------------------------- maturities (D1, D44, P49)
    def _maturities(self) -> None:
        split = float(self.ctx.resolve("rates.floating_split_usd"))
        usd = {e.dates["maturity_date"]: e for e in items(self.exposures, "rates", "maturity")}
        eur = {e.dates["maturity_date"]: e for e in items(self.exposures, "fx", "maturity")}
        for when in sorted(set(usd) | set(eur)):
            row = {"date": when, "usd": 0.0, "usd_floating": 0.0, "usd_fixed": 0.0, "eur": 0.0,
                   "eur_usd_at_disclosure": 0.0, "usd_item": usd.get(when), "eur_item": eur.get(when)}
            if when in usd:
                item = usd[when]
                m, f = float(item.quantities["amount"]), float(item.quantities["floating_share"])
                phi = f * split / (f * split + (1 - f)) if f else 0.0
                row.update(usd=m, usd_floating=m * phi, usd_fixed=m * (1 - phi))
            if when in eur:
                item = eur[when]
                row.update(eur=float(item.quantities["amount"]),
                           eur_usd_at_disclosure=float(item.quantities["amount_usd_at_disclosure"]))
            self.state.maturities.append(row)

    # ---------------------------------------------------------------- floating tranches
    def _floating(self) -> None:
        tranches = items(self.exposures, "rates", "float")
        by_ccy: dict[Currency, list[ExposureItem]] = defaultdict(list)
        for t in tranches:
            by_ccy[Currency(t.currency)].append(t)
        for ccy, group in by_ccy.items():
            total = sum(float(t.quantities["notional"]) for t in group)
            for t in group:
                notional = Notional(float(t.quantities["notional"]))
                share = float(t.quantities["notional"]) / total
                for row in self.state.maturities:
                    amount = row["usd_floating"] if ccy is Currency.USD else row["eur"]
                    if amount:
                        notional.reductions.append((row["date"], amount * share))
                resets = sorted(t.dates["resets"])
                months = int(t.quantities["reset_months"])
                self.state.float_notional[t.sub_id] = notional
                self.state.float_currency[t.sub_id] = ccy
                self.state.float_resets[t.sub_id] = [add_months(resets[0], -months), *resets]
                self._floating_lines(t.sub_id, ccy, notional, resets, months, float(t.quantities["spread_bp"]),
                                     LineCode.INTEREST_EXPENSE, "expense")

    def _floating_lines(self, sub_id: str, ccy: Currency, notional: Notional, resets: list[date], months: int,
                        spread_bp: float, code: LineCode, kind: str, first_start: date | None = None) -> None:
        spans = periods(resets, months) if first_start is None else list(zip(resets, resets[1:]))
        for start, end in spans:
            bench = self.market.fixing(ccy, start)
            if bench < 0:
                self.state.floors.append({"sub_id": sub_id, "date": start, "benchmark_pct": bench})
            rate = max(bench, 0.0) + spread_bp / 100
            interest = accrue(notional, start, end, rate)
            if interest:
                self.emit(sub_id, code, ccy, end, -interest, A, kind)

    # ---------------------------------------------------------------- deposits
    def _zim_cash_part(self) -> None:
        """P63: when the switch is on, consideration - drawdown leaves the USD deposits on the draw date."""
        if not self.params.rates.zim_tranche_on:
            return
        for z in items(self.exposures, "rates", "zim"):
            cash = float(z.quantities.get("consideration_usd", z.quantities["notional"])) - float(z.quantities["notional"])
            if cash > 0:
                self.state.zim_cash_part = (z.dates["draw_date"], cash)

    def _deposits(self) -> None:
        deposits = items(self.exposures, "rates", "deposit")
        usd_total = sum(float(d.quantities["balance_usd"]) for d in deposits if Currency(d.currency) is Currency.USD)
        for d in deposits:
            ccy = Currency(d.currency)
            balance_usd = float(d.quantities["balance_usd"])
            fx = 1.0 if ccy is Currency.USD else self.market.fx_t0()
            usd_notional = Notional(balance_usd)
            if ccy is Currency.USD and self.state.zim_cash_part and usd_total:
                when, cash = self.state.zim_cash_part
                cut = min(cash * balance_usd / usd_total, balance_usd)  # never below zero
                if cut < cash * balance_usd / usd_total:
                    self.state.missing.append(MissingInput(key="rates.zim_consideration_usd", status="exceeds_usd_deposits",
                                                           affects=f"{d.sub_id} balance floored at zero"))
                usd_notional.reductions.append((when, cut))
            balance = Notional(balance_usd / fx, [(w, a / fx) for w, a in usd_notional.reductions])
            rolls = sorted(d.dates["rolls"])
            months = int(d.quantities["roll_months"])
            below = float(d.quantities["spread_below_bp"]) / 100
            self.state.deposit_balance[d.sub_id] = balance.base
            self.state.deposit_balance_usd[d.sub_id] = usd_notional
            self.state.deposit_currency[d.sub_id] = ccy
            self.state.deposit_rolls[d.sub_id] = [add_months(rolls[0], -months), *rolls]
            for start, end in periods(rolls, months):
                bench = self.market.fixing(ccy, start)
                if bench < 0:
                    self.state.floors.append({"sub_id": d.sub_id, "date": start, "benchmark_pct": bench})
                yield_pct = max(max(bench, 0.0) - below, 0.0)
                interest = accrue(balance, start, end, yield_pct)  # day by day: the balance can fall (P63)
                if interest:
                    self.emit(d.sub_id, LineCode.INTEREST_INCOME, ccy, end, interest, A, "income")

    # ---------------------------------------------------------------- fixed debt and the bullet bond
    def _fixed(self) -> None:
        for f in items(self.exposures, "rates", "fixed"):
            ccy = Currency(f.currency)
            notional = float(f.quantities["notional"])
            if f.sub_id.endswith(":bond"):
                maturity = self.ctx.resolve("rates.bond_maturity_date")
                coupon = float(f.quantities["coupon_pct"])  # annual rate
                per_year = int(f.quantities.get("coupons_per_year", 1))  # 2 for semi-annual (P60)
                for when in f.dates.get("coupons", []):
                    if when <= maturity:  # bullet: full notional until maturity (P49)
                        self.emit(f.sub_id, LineCode.INTEREST_EXPENSE, ccy, when,
                                  -notional * coupon / 100 / per_year, A, "expense")
                continue
            rate = float(f.quantities["rate_pct"])
            outstanding = Notional(notional, [(r["date"], r["usd_fixed"]) for r in self.state.maturities if r["usd_fixed"]])
            self.state.fixed_usd = outstanding
            for when in f.dates.get("payments", []):
                days = list(daterange(when.replace(day=1), month_end(when)))
                average = sum(outstanding.outstanding(d) for d in days) / len(days)
                self.emit(f.sub_id, LineCode.INTEREST_EXPENSE, ccy, when, -average * rate / 100 / 12, A, "expense")

    def _lease(self) -> None:
        for lease in items(self.exposures, "rates", "lease"):
            monthly = float(lease.quantities["monthly_interest"])
            self.state.lease_monthly = monthly
            for when in lease.dates.get("payments", []):
                self.emit(lease.sub_id, LineCode.INTEREST_EXPENSE, Currency(lease.currency), when, -monthly,
                          ProvenanceLabel.weakest(lease.label), "lease")

    # ---------------------------------------------------------------- refinancing (D1, P23)
    def new_issue_spread_bp(self, on: date) -> float:
        bb = self.market.value("BB_OAS") * 100
        premium = float(self.ctx.resolve("rates.new_issue_premium_bp", on))
        return bb + premium + self.params.rates.credit_spread_change_bp * shape_factor(self.spec, on)

    def _refinancing(self) -> None:
        if not self.state.maturities:
            return
        _, h_end = self.ctx.horizon
        rollover = self.params.rates.refinancing_mode is RefinancingMode.ROLLOVER
        for row in self.state.maturities:
            when = row["date"]
            usd_item = row["usd_item"]
            if usd_item is not None:
                self.emit(usd_item.sub_id, LineCode.DEBT_PRINCIPAL, Currency.USD, when, -row["usd"], usd_item.label)
            if not rollover:
                continue
            drawn = 0.0
            if usd_item is not None:
                self.emit(f"rates:drawdown:USD:{ym(when)}", LineCode.DEBT_DRAWDOWN, Currency.USD, when, row["usd"],
                          usd_item.label)
                drawn += row["usd"]
            if row["eur_item"] is not None:
                eur_usd = row["eur"] * self.market.fx_t0()  # state value, identical in both passes (P23)
                self.emit(f"rates:drawdown:EUR:{ym(when)}", LineCode.DEBT_DRAWDOWN, Currency.USD, when, eur_usd,
                          row["eur_item"].label)
                drawn += eur_usd
            if not drawn:
                continue
            self.state.refi.append((when, drawn))
            spread = self.new_issue_spread_bp(when)
            resets = step_dates(when, 3, add_months(h_end, 3))
            self._floating_lines(f"rates:refi:{ym(when)}", Currency.USD, Notional(drawn), resets, 3, spread,
                                 LineCode.REFINANCING_COST, "refi", first_start=when)

    # ---------------------------------------------------------------- ZIM tranche (state switch)
    def _zim(self) -> None:
        """When on: drawdown of the external financing, the acquisition payment and floating interest (P63)."""
        if not self.params.rates.zim_tranche_on:
            return
        _, h_end = self.ctx.horizon
        for z in items(self.exposures, "rates", "zim"):
            draw = z.dates["draw_date"]
            notional = float(z.quantities["notional"])
            consideration = float(z.quantities.get("consideration_usd", notional))
            spread = float(z.quantities["spread_bp"])
            if draw > self.ctx.as_of:
                spread += self.params.rates.credit_spread_change_bp * shape_factor(self.spec, draw)
                # state cash flows, identical in both passes (BASE_AMOUNT)
                self.emit(z.sub_id, LineCode.DEBT_DRAWDOWN, Currency.USD, draw, notional, z.label)
                self.emit(z.sub_id, LineCode.ACQUISITION_PAYMENT, Currency.USD, draw, -consideration, z.label)
            self.state.zim = (draw, notional)
            resets = step_dates(draw, 3, add_months(h_end, 3))
            self._floating_lines(z.sub_id, Currency.USD, Notional(notional), resets, 3, spread,
                                 LineCode.INTEREST_EXPENSE, "zim", first_start=draw)
