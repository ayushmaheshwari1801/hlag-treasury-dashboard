"""Liquidity path, tiers and liquidity tests (spec 3.7, 4.4), P77-P79.

Starting liquidity (P77): T1(T0) = cash at the last disclosure (30 Jun 2026) + for each day from the day after
to T0 the OCF run-rate and the net interest at the H1 run-rate (interest sits outside OCF while
interest_in_ocf = false) - lease repayments / 12 and monthly capex at each month end in that window. Maturities
there net to zero under rollover; no dividend falls in it. Only profile values are used, so the roll-forward
does not depend on any module (no circularity). Labelled DERIVED; the components go into the run record.

Baseline flow on each path date (3.7): B(t) = OCF x n(t) + lease + capex + base-pass USD of BASE_AMOUNT lines,
plus the anchor adjustment A(t). Liquidity L_b(t) = L_b(t-1) + B(t); L_s(t) = L_b(t) + cumulative delta(t).
Path dates: days 1-91 daily, then month ends; n(t) = days since the previous path date.

Tiers (D22): T1 = L(t) minus availability cuts; T2 = credit lines minus cuts; T3 = special fund, counted only
when count_liquid_investments; T4 = the margin-eligible share of T1. A cut takes the counterparty's share of the
tier balance on its effective date times the drop in its available fraction, and is restored when the
adjustment expires (P78). Headroom H(t) = T1 + T2 [+ T3] - reserve.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from hlag_engine.company_profile.registers import Registers
from hlag_engine.company_profile.resolve import RunContext, resolve
from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import BaselineTreatment, LineCode, ShortfallTier
from hlag_engine.engine.timeline import DAILY_DAYS, snap_date
from hlag_engine.generator.calendar import horizon, month_end, months_between

ZERO = Decimal(0)


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(repr(float(x)))


@dataclass
class LiquidityInputs:
    t1_start: Decimal
    t2_start: Decimal
    t3_start: Decimal
    reserve: Decimal
    ocf_daily: Decimal
    count_t3: bool = False
    t4_share: Decimal = Decimal("0.25")
    lease: list[tuple[date, Decimal]] = field(default_factory=list)  # negative amounts
    capex: list[tuple[date, Decimal]] = field(default_factory=list)  # negative amounts
    roll_forward: dict | None = None  # components of T1(T0), DERIVED (P77)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lease"] = [[w.isoformat(), str(a)] for w, a in self.lease]
        d["capex"] = [[w.isoformat(), str(a)] for w, a in self.capex]
        for k in ("t1_start", "t2_start", "t3_start", "reserve", "ocf_daily", "t4_share"):
            d[k] = str(d[k])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LiquidityInputs":
        return cls(
            t1_start=Decimal(d["t1_start"]), t2_start=Decimal(d["t2_start"]), t3_start=Decimal(d["t3_start"]),
            reserve=Decimal(d["reserve"]), ocf_daily=Decimal(d["ocf_daily"]), count_t3=bool(d["count_t3"]),
            t4_share=Decimal(d["t4_share"]),
            lease=[(date.fromisoformat(w), Decimal(a)) for w, a in d["lease"]],
            capex=[(date.fromisoformat(w), Decimal(a)) for w, a in d["capex"]],
            roll_forward=d.get("roll_forward"),
        )


def from_profile(resolver: RunContext) -> LiquidityInputs:
    """Liquidity inputs from the profile, with T1 rolled forward from the last disclosure to T0 (P77)."""
    as_of = resolver.as_of
    profile = resolver.profile

    def r(key):
        return resolve(key, as_of, resolver)

    cash_entry = profile.entry("liquidity.cash_and_equivalents_usd")
    disclosed = cash_entry.as_of
    cash = D(r("liquidity.cash_and_equivalents_usd"))
    ocf = D(r("liquidity.baseline_ocf_daily_usd"))
    lease_month = D(r("liquidity.annual_lease_repayments_usd")) / 12
    capex_month = D(r("liquidity.capex_monthly_usd"))
    gap_days = (as_of - disclosed).days
    gap_month_ends = [month_end(m) for m in months_between(disclosed + timedelta(days=1), as_of)
                      if disclosed < month_end(m) <= as_of]
    snap = profile.anchor_snapshot
    period_days = (snap.end - snap.start).days + 1
    interest_daily = ZERO
    if not r("liquidity.interest_in_ocf"):
        interest_daily = (D(r("rates.money_market_income_h1_usd")) - D(r("rates.interest_expense_h1_usd"))) / period_days
    components = {
        "cash_disclosed": cash, "disclosed_on": disclosed, "days": gap_days, "month_ends": len(gap_month_ends),
        "ocf": ocf * gap_days, "net_interest": interest_daily * gap_days,
        "lease": -lease_month * len(gap_month_ends), "capex": -capex_month * len(gap_month_ends),
    }
    t1 = cash + components["ocf"] + components["net_interest"] + components["lease"] + components["capex"]
    components["t1_at_t0"] = t1
    components["label"] = "DERIVED"
    _, h_end = horizon(as_of)
    month_ends = [month_end(m) for m in months_between(as_of + timedelta(days=1), h_end)]
    return LiquidityInputs(
        t1_start=t1, t2_start=D(r("liquidity.unused_credit_lines_usd")), t3_start=D(r("liquidity.special_fund_usd")),
        reserve=D(r("liquidity.minimum_operational_reserve_usd")), ocf_daily=ocf,
        count_t3=bool(r("liquidity.count_liquid_investments")), t4_share=D(r("liquidity.margin_eligible_share_of_T1")),
        lease=[(d, -lease_month) for d in month_ends], capex=[(d, -capex_month) for d in month_ends],
        roll_forward={k: (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, date) else v)
                      for k, v in components.items()},
    )


# ---------------------------------------------------------------- line treatment (2.2, D34, D51)


@dataclass(frozen=True)
class TreatmentFlags:
    interest_in_ocf: bool
    lease_includes_interest: bool
    capex_includes_fx_transaction: bool

    @classmethod
    def from_resolver(cls, resolver: RunContext) -> "TreatmentFlags":
        def r(key):
            return bool(resolve(key, resolver.as_of, resolver))
        return cls(r("liquidity.interest_in_ocf"), r("liquidity.lease_repayments_include_interest"),
                   r("liquidity.capex_includes_fx_transaction"))


def line_treatment(line: CashLine, flags: TreatmentFlags) -> BaselineTreatment:
    """BASE_AMOUNT or DELTA_ONLY for one line (2.2): the LEASE line is DELTA_ONLY when the lease repayment figure
    already includes interest (D51), otherwise interest lines follow interest_in_ocf; FX_TRANSACTION is DELTA_ONLY
    when capex already includes it (D34)."""
    t = line.line_code.baseline_treatment
    if t is BaselineTreatment.PER_INTEREST_IN_OCF:
        if line.sub_id.endswith("LEASE") and flags.lease_includes_interest:
            return BaselineTreatment.DELTA_ONLY
        return BaselineTreatment.DELTA_ONLY if flags.interest_in_ocf else BaselineTreatment.BASE_AMOUNT
    if t is BaselineTreatment.PER_CAPEX_BASELINE:
        return BaselineTreatment.DELTA_ONLY if flags.capex_includes_fx_transaction else BaselineTreatment.BASE_AMOUNT
    return t


# ---------------------------------------------------------------- path


def path_dates(as_of: date) -> list[date]:
    _, h_end = horizon(as_of)
    days = [as_of + timedelta(days=i) for i in range(1, DAILY_DAYS + 1)]
    month_ends = [month_end(m) for m in months_between(days[-1] + timedelta(days=1), h_end)]
    return days + [d for d in month_ends if d > days[-1]]


def on_path(items: Iterable[tuple[date, Decimal]], as_of: date) -> dict[date, Decimal]:
    out: dict[date, Decimal] = defaultdict(Decimal)
    _, h_end = horizon(as_of)
    for d, amount in items:
        if as_of < d <= h_end:
            out[snap_date(d, as_of)] += amount
    return out


@dataclass
class Paths:
    dates: list[date]
    baseline_flow: dict[date, Decimal]  # B(t) without A
    anchor: dict[date, Decimal]  # A(t)
    delta: dict[date, Decimal]  # scenario delta per date
    l_base: dict[date, Decimal]
    l_scen: dict[date, Decimal]


def baseline_flows(inputs: LiquidityInputs, base_lines: Sequence[CashLine], flags: TreatmentFlags,
                   as_of: date, dates: list[date]) -> dict[date, Decimal]:
    flow: dict[date, Decimal] = defaultdict(Decimal)
    previous = as_of
    for d in dates:
        flow[d] += inputs.ocf_daily * (d - previous).days
        previous = d
    for d, a in on_path(inputs.lease, as_of).items():
        flow[d] += a
    for d, a in on_path(inputs.capex, as_of).items():
        flow[d] += a
    for line in base_lines:
        if line_treatment(line, flags) is BaselineTreatment.BASE_AMOUNT:
            flow[line.date] += line.amount_usd_base
    return dict(flow)


def build_paths(inputs: LiquidityInputs, base_lines, flags, as_of: date, delta_by_date: dict[date, Decimal],
                anchor: dict[date, Decimal] | None = None) -> Paths:
    dates = path_dates(as_of)
    flow = baseline_flows(inputs, base_lines, flags, as_of, dates)
    anchor = anchor or {}
    l_b, l_s = {}, {}
    level, cum = inputs.t1_start, ZERO
    for d in dates:
        level += flow.get(d, ZERO) + anchor.get(d, ZERO)
        cum += delta_by_date.get(d, ZERO)
        l_b[d], l_s[d] = level, level + cum
    return Paths(dates, flow, anchor, delta_by_date, l_b, l_s)


# ---------------------------------------------------------------- tiers and availability cuts (D22, D30, P19, P78)


@dataclass
class Tiers:
    t1: dict[date, Decimal]
    t2: dict[date, Decimal]
    t3: dict[date, Decimal]
    t4: dict[date, Decimal]
    headroom: dict[date, Decimal]
    cuts: list[dict]


def tiers(levels: dict[date, Decimal], inputs: LiquidityInputs, resolver: RunContext | None,
          registers: Registers | None) -> Tiers:
    dates = sorted(levels)
    shares = {"T1": {}, "T2": {}}
    if registers is not None and resolver is not None:
        for c in registers.counterparties.counterparties:
            shares["T1"][c.id] = D(c.deposit_share)
            shares["T2"][c.id] = D(c.credit_line_share)
    fraction = {tier: {k: Decimal(1) for k in shares[tier]} for tier in shares}
    outstanding = {tier: {k: ZERO for k in shares[tier]} for tier in shares}
    cut_total = {"T1": ZERO, "T2": ZERO}
    t1, t2, t3, t4, head, events = {}, {}, {}, {}, {}, []
    for d in dates:
        for tier in ("T1", "T2"):
            balance = (levels[d] if tier == "T1" else inputs.t2_start) - cut_total[tier]
            for k, share in shares[tier].items():
                new = D(resolve(f"liquidity.{tier}.{k}", d, resolver))
                old = fraction[tier][k]
                if new < old:
                    amount = share * (old - new) * max(balance, ZERO)
                    outstanding[tier][k] += amount
                    cut_total[tier] += amount
                    events.append({"tier": tier, "counterparty": k, "date": d, "cut": amount})
                elif new > old and outstanding[tier][k]:
                    back = outstanding[tier][k] * (new - old) / (1 - old)
                    outstanding[tier][k] -= back
                    cut_total[tier] -= back
                    events.append({"tier": tier, "counterparty": k, "date": d, "restored": back})
                fraction[tier][k] = new
        t1[d] = levels[d] - cut_total["T1"]
        t2[d] = inputs.t2_start - cut_total["T2"]
        t3[d] = inputs.t3_start
        t4[d] = inputs.t4_share * t1[d]
        head[d] = t1[d] + t2[d] + (t3[d] if inputs.count_t3 else ZERO) - inputs.reserve
    return Tiers(t1, t2, t3, t4, head, events)


# ---------------------------------------------------------------- liquidity tests (4.4)


@dataclass(frozen=True)
class Minimum:
    value: Decimal
    date: date


def minimum(headroom: dict[date, Decimal]) -> Minimum:
    d = min(sorted(headroom), key=lambda x: headroom[x])  # earliest date among ties
    return Minimum(headroom[d], d)


def consumed_pct(h_b: Decimal, h_s: Decimal) -> Decimal | None:
    return None if h_b <= 0 else (h_b - h_s) / h_b * 100


def first_breach(*headrooms: dict[date, Decimal]) -> date | None:
    dates = [d for h in headrooms for d in sorted(h) if h[d] < 0]
    return min(dates) if dates else None


def shortfall_tier(t: Tiers, d: date, inputs: LiquidityInputs) -> ShortfallTier:
    t1, t12 = t.t1[d], t.t1[d] + t.t2[d]
    if t1 >= inputs.reserve:
        return ShortfallTier.WITHIN_OPERATING_CASH
    if t12 >= inputs.reserve:
        return ShortfallTier.CREDIT_LINES_NEEDED
    if inputs.count_t3 and t12 + t.t3[d] >= inputs.reserve:
        return ShortfallTier.LIQUID_INVESTMENTS_NEEDED
    return ShortfallTier.RESERVE_BREACHED


@dataclass(frozen=True)
class MarginTest:
    lowest: Decimal
    lowest_date: date | None
    shortfall_date: date | None

    @property
    def passed(self) -> bool:
        return self.shortfall_date is None


def margin_test(t: Tiers, margin_lines: Sequence[CashLine], usd_attr: str, as_of: date) -> MarginTest:
    """T4(t) minus the cumulative MARGIN_CALL outflow; a negative value is a margin-eligible shortfall (4.4)."""
    by_date = defaultdict(Decimal)
    for line in margin_lines:
        if line.line_code is LineCode.MARGIN_CALL:
            by_date[snap_date(line.date, as_of)] += getattr(line, usd_attr)
    outflow, lowest, lowest_date, short = ZERO, None, None, None
    for d in sorted(t.t4):
        outflow -= by_date.get(d, ZERO)
        value = t.t4[d] - outflow
        if lowest is None or value < lowest:
            lowest, lowest_date = value, d
        if value < 0 and short is None:
            short = d
    return MarginTest(lowest if lowest is not None else ZERO, lowest_date, short)
