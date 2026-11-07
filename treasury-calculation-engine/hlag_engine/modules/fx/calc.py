"""FX module calculation (spec 9B.3, 9B.4): own lines and policy hedge specs.

Lines (native, negative): FX_OPERATING_COST (CAD and EUR operating costs), DIVIDEND (EUR),
DEBT_PRINCIPAL (EUR share of the single maturity schedule, floating-only, P49) and FX_TRANSACTION
(INR and BRL obligations, only while fx.inr_brl_obligations_on). CNY and GBP memo positions are
never cash lines (D19).

Policy hedge specs (P65-P68): one per hedged line; the ratio is resolved on the contract start date.
- CAD programme: start 91 days (13 weeks) before payment, ratio fx.hedge_ratio.cad, cap 80%.
- EUR debt principal: start T0, ratio fx.hedge_ratio.eur_debt.
- INR/BRL: locked at the 31 Dec 2025 rate (fx.lock_rate_usd_per_unit.<ccy>); while that is a
  placeholder the hedges are skipped and the key is reported missing.
- EUR operating costs and dividend: ratio fx.hedge_ratio.eur_operating_costs / eur_dividend
  (0 by default), start T0 (or the hedge test's start date).
A hedge test (module fx, instrument forward) replaces its target policy's ratio with coverage_pct for
lines dated from start_date to start_date + tenor_months.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from hlag_engine.company_profile.schema import MissingProfileValue
from hlag_engine.contracts.enums import Currency, HedgeInstrument, HedgeModule, LineCode, OwnerSpec, ProvenanceLabel
from hlag_engine.contracts.exposure import ExposureItem, MissingInput
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.policy_hedge import CAD_RATIO_CAP, HedgePolicy, PolicyHedgeSpec
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.generator.calendar import add_months
from hlag_engine.modules.interface import LineBook, ModuleInputError, ModuleRunContext

CAD_HEDGE_LEAD = timedelta(days=91)  # 13 weeks before payment (9B.4)
LOCK_RATE_DATE = date(2025, 12, 31)
RATIO_KEYS = {
    HedgePolicy.CAD_OPERATING_COSTS: "fx.hedge_ratio.cad",
    HedgePolicy.EUR_DEBT_PRINCIPAL: "fx.hedge_ratio.eur_debt",
    HedgePolicy.INR_BRL_OBLIGATIONS: "fx.hedge_ratio.inr_brl",
    HedgePolicy.EUR_OPERATING_COSTS: "fx.hedge_ratio.eur_operating_costs",
    HedgePolicy.EUR_DIVIDEND: "fx.hedge_ratio.eur_dividend",
}


@dataclass
class HedgedLine:
    policy: HedgePolicy
    sub_id: str
    code: LineCode
    currency: Currency
    date: date


@dataclass
class FxState:
    book: LineBook
    hedged: list[HedgedLine] = field(default_factory=list)
    specs: list[PolicyHedgeSpec] = field(default_factory=list)
    missing: list[MissingInput] = field(default_factory=list)
    memo_usd: dict[Currency, float] = field(default_factory=dict)
    memo_native: dict[Currency, float] = field(default_factory=dict)
    derived: dict = field(default_factory=dict)  # history-derived figures, stored for replay (P71)


def fx_tests(tests: list[HedgeTest]) -> dict[HedgePolicy, HedgeTest]:
    out = {}
    for test in tests:
        if test.module is not HedgeModule.FX:
            continue
        try:
            policy = HedgePolicy(test.target)
        except ValueError:
            raise ModuleInputError(f"fx hedge target {test.target!r} is not one of {[p.value for p in HedgePolicy]}") from None
        if test.instrument is not HedgeInstrument.FORWARD:
            raise ModuleInputError(f"fx hedge instrument must be forward, got {test.instrument.value!r}")
        if test.strike_rule != "spot_at_T0":
            raise ModuleInputError(f"strike rule {test.strike_rule!r} is not supported (spot_at_T0 only)")
        if policy is HedgePolicy.CAD_OPERATING_COSTS and test.coverage_pct / 100 > CAD_RATIO_CAP:
            raise ModuleInputError(f"CAD hedge ratio {test.coverage_pct}% is above the 80% cap")
        out[policy] = test
    return out


def emit_lines(ctx: ModuleRunContext, exposures: list[ExposureItem], obligations_on: bool) -> FxState:
    state = FxState(LineBook(OwnerSpec.FX))
    for item in exposures:
        if item.owner_spec is not OwnerSpec.FX:
            continue
        ccy = Currency(item.currency) if item.currency else None
        label = ProvenanceLabel.weakest(item.label)
        if item.kind == "opex":
            when = item.dates["payment_date"]
            state.book.add(item.sub_id, LineCode.FX_OPERATING_COST, ccy, when, -float(item.quantities["amount"]), label)
            policy = HedgePolicy.CAD_OPERATING_COSTS if ccy is Currency.CAD else HedgePolicy.EUR_OPERATING_COSTS
            state.hedged.append(HedgedLine(policy, item.sub_id, LineCode.FX_OPERATING_COST, ccy, when))
        elif item.kind == "dividend":
            when = item.dates["payment_date"]
            state.book.add(item.sub_id, LineCode.DIVIDEND, ccy, when, -float(item.quantities["amount"]), label)
            state.hedged.append(HedgedLine(HedgePolicy.EUR_DIVIDEND, item.sub_id, LineCode.DIVIDEND, ccy, when))
        elif item.kind == "maturity":
            when = item.dates["maturity_date"]
            state.book.add(item.sub_id, LineCode.DEBT_PRINCIPAL, ccy, when, -float(item.quantities["amount"]), label)
            state.hedged.append(HedgedLine(HedgePolicy.EUR_DEBT_PRINCIPAL, item.sub_id, LineCode.DEBT_PRINCIPAL, ccy, when))
        elif item.kind == "obligation":
            if not obligations_on:
                continue
            when = item.dates["payment_date"]
            state.book.add(item.sub_id, LineCode.FX_TRANSACTION, ccy, when, -float(item.quantities["amount"]), label)
            state.hedged.append(HedgedLine(HedgePolicy.INR_BRL_OBLIGATIONS, item.sub_id, LineCode.FX_TRANSACTION, ccy, when))
        elif item.kind == "memo":
            state.memo_usd[ccy] = float(item.quantities["position_usd"])
    return state


def build_specs(ctx: ModuleRunContext, params: ScenarioParams, state: FxState) -> None:
    tests = fx_tests(params.hedge_tests)
    lock_rates: dict[Currency, float | None] = {}
    for h in state.hedged:
        test = tests.get(h.policy)
        in_test = test is not None and test.start_date <= h.date < add_months(test.start_date, test.tenor_months)
        if h.policy is HedgePolicy.CAD_OPERATING_COSTS:
            start = h.date - CAD_HEDGE_LEAD
        elif h.policy in (HedgePolicy.EUR_OPERATING_COSTS, HedgePolicy.EUR_DIVIDEND) and in_test:
            start = test.start_date
        elif h.policy is HedgePolicy.INR_BRL_OBLIGATIONS:
            start = LOCK_RATE_DATE
        else:
            start = ctx.as_of
        ratio = test.coverage_pct / 100 if in_test else float(ctx.resolve(RATIO_KEYS[h.policy], start))
        if h.policy is HedgePolicy.CAD_OPERATING_COSTS and ratio > CAD_RATIO_CAP:
            raise ModuleInputError(f"CAD hedge ratio {ratio} is above the 80% cap")
        if ratio <= 0:
            continue
        locked = None
        if h.policy is HedgePolicy.INR_BRL_OBLIGATIONS:
            if h.currency not in lock_rates:
                key = f"fx.lock_rate_usd_per_unit.{h.currency.value}"
                try:
                    lock_rates[h.currency] = float(ctx.resolve(key))
                except MissingProfileValue as exc:
                    lock_rates[h.currency] = None
                    state.missing.append(MissingInput(key=exc.key, status=exc.status,
                                                      affects=f"{h.currency.value} obligation hedges"))
            locked = lock_rates[h.currency]
            if locked is None:
                continue
        state.specs.append(PolicyHedgeSpec(policy=h.policy, currency=h.currency, hedged_sub_id=h.sub_id,
                                           hedged_line_code=h.code, ratio=ratio, start_date=start, locked_rate=locked))


def memo_native(ctx: ModuleRunContext, state: FxState) -> None:
    for ccy, usd in state.memo_usd.items():
        sid = f"FX_USD_PER_{ccy.value}"
        if sid not in ctx.market:
            state.missing.append(MissingInput(key=sid, status="missing_market_data", affects=f"{ccy.value} memo position"))
            continue
        state.memo_native[ccy] = usd / ctx.market[sid].value
