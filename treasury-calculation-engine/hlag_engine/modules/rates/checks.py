"""Rates back-tests and scope checks (spec 8.13). Separate from the cash engine."""

from dataclasses import dataclass, field

from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import Currency, LineCode
from hlag_engine.contracts.exposure import ExposureItem

STATIC_TOLERANCE = 0.10  # PROPOSED ILLUSTRATIVE
EXPENSE_TOLERANCE, INCOME_TOLERANCE = 0.25, 0.30


@dataclass(frozen=True)
class StaticCheck:
    """Static +-100 bp on variable debt (D10): notional x 1% x 1 year, no reset lag, zero floor."""

    up_effect: float  # earnings effect of +100 bp (negative: higher interest)
    down_effect: float  # earnings effect of -100 bp
    published_up: float
    published_down: float

    @property
    def up_diff(self) -> float:
        return self.up_effect / self.published_up - 1

    @property
    def down_diff(self) -> float:
        return self.down_effect / self.published_down - 1

    @property
    def signs_match(self) -> bool:
        return (self.up_effect < 0) == (self.published_up < 0) and (self.down_effect > 0) == (self.published_down > 0)

    @property
    def passed(self) -> bool:
        return self.signs_match and abs(self.up_diff) <= STATIC_TOLERANCE and abs(self.down_diff) <= STATIC_TOLERANCE


def static_sensitivity(variable_debt: float, published_up: float, published_down: float,
                       benchmark_pct: float | None = None) -> StaticCheck:
    """With benchmark_pct, the zero floor limits how far a cut can lower the rate."""
    def effect(shift_pct: float) -> float:
        if benchmark_pct is None:
            change = shift_pct
        else:
            change = max(benchmark_pct + shift_pct, 0.0) - max(benchmark_pct, 0.0)
        return -variable_debt * change / 100

    return StaticCheck(effect(1.0), effect(-1.0), published_up, published_down)


@dataclass
class CalibrationCheck:
    """Annualised interest at T0 rates against twice the reported H1 figures. Reported, never failed."""

    expense_annual_usd: float
    expense_target_usd: float
    income_annual_usd: float
    income_target_usd: float
    adjustment_order: dict[str, list[str]] = field(default_factory=lambda: {
        "deposit_income": ["rates.deposit_spread_bp", "rates.deposit_split_usd", "rates.interest_bearing_cash_share"],
        "interest_expense": ["rates.spread_usd_bp", "rates.spread_eur_bp"],
    })

    @property
    def expense_gap(self) -> float:
        return self.expense_annual_usd / self.expense_target_usd - 1

    @property
    def income_gap(self) -> float:
        return self.income_annual_usd / self.income_target_usd - 1

    @property
    def expense_within(self) -> bool:
        return abs(self.expense_gap) <= EXPENSE_TOLERANCE

    @property
    def income_within(self) -> bool:
        return abs(self.income_gap) <= INCOME_TOLERANCE


def calibration_check(exposures: list[ExposureItem], sofr_pct: float, estr_pct: float, usd_per_eur: float,
                      expense_h1_usd: float, income_h1_usd: float) -> CalibrationCheck:
    bench = {Currency.USD: sofr_pct, Currency.EUR: estr_pct}
    fx = {Currency.USD: 1.0, Currency.EUR: usd_per_eur}
    expense = income = 0.0
    for e in exposures:
        if e.owner_spec.value != "rates":
            continue
        ccy = Currency(e.currency) if e.currency else Currency.USD
        q = e.quantities
        if e.kind == "float":
            expense += float(q["notional"]) * (max(bench[ccy], 0) + float(q["spread_bp"]) / 100) / 100 * fx[ccy]
        elif e.kind == "fixed":
            rate = float(q.get("rate_pct", q.get("coupon_pct", 0)))
            expense += float(q["notional"]) * rate / 100 * fx[ccy]
        elif e.kind == "lease":
            expense += float(q["monthly_interest"]) * 12
        elif e.kind == "deposit":
            income += float(q["balance_usd"]) * max(max(bench[ccy], 0) - float(q["spread_below_bp"]) / 100, 0) / 100
    return CalibrationCheck(expense, 2 * expense_h1_usd, income, 2 * income_h1_usd)


def scope_problems(exposures: list[ExposureItem], lines: list[CashLine], horizon_end) -> list[str]:
    """Every line has a currency; resets inside the horizon; leases never in refinancing."""
    problems = []
    for line in lines:
        if line.currency is None:
            problems.append(f"{line.sub_id} has no currency")
        if line.line_code in (LineCode.REFINANCING_COST, LineCode.DEBT_DRAWDOWN) and "LEASE" in line.sub_id:
            problems.append(f"lease in refinancing: {line.sub_id}")
    for e in exposures:
        for key in ("resets", "rolls"):
            dates = e.dates.get(key, [])
            if any(d > horizon_end for d in dates):
                problems.append(f"{e.sub_id} has {key} after the horizon")
            if list(dates) != sorted(dates):
                problems.append(f"{e.sub_id} {key} are not in order")
    return problems
