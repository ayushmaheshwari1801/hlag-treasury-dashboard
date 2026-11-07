"""Module exposure recipes (spec 6.3, 7.3, 8.3, 9B.3). Static quantities only, no shocks.

Each item carries synthetic = true, the label of the figures that sized it (weakest-label
rule) and a short sizing note. sub_ids are <module>:<kind>:<key>.

Dates the spec leaves open use these conventions (listed in docs/placeholders.md):
floating resets and deposit rolls on the first day of the month; monthly interest,
lease interest and INR/BRL payments on the last day of the month; weekly CAD and EUR costs
on Fridays.
"""

from datetime import date

from hlag_engine.contracts.enums import Currency, OwnerSpec, ProvenanceLabel, Trade
from hlag_engine.contracts.exposure import ExposureItem
from hlag_engine.generator.calendar import (
    add_months,
    days_in_month,
    days_in_quarter,
    every_n_months,
    firsts_after,
    month_end,
    months_between,
    overlap_days,
    quarter_of,
    weekdays_between,
)
from hlag_engine.generator.inputs import Inputs
from hlag_engine.generator.maturities import MaturitySchedule
from hlag_engine.company_profile.schema import MissingProfileValue

A, D, X = ProvenanceLabel.ASSUMPTION, ProvenanceLabel.DERIVED, ProvenanceLabel.DISCLOSED
FRIDAY = 4
HUBS = ("SIN", "RTM")
GRADES = ("VLSFO", "HSFO")


def _item(owner: OwnerSpec, kind: str, key: str, label: ProvenanceLabel, note: str, **fields) -> ExposureItem:
    return ExposureItem(
        sub_id=f"{owner.value}:{kind}:{key}", owner_spec=owner, kind=kind, label=label, sizing_note=note, **fields
    )


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ------------------------------------------------------------------------ freight (6.3)


def quarter_volume(inputs: Inputs, year: int, quarter: int) -> tuple[float, ProvenanceLabel, str]:
    """Group transport volume for a quarter (P16).

    A quarter with a profile entry uses it (a to_enter placeholder raises). A quarter not yet
    published takes the latest earlier same quarter in the profile x (1 + g) per missing year,
    labelled ASSUMPTION.
    """
    key = f"freight.group_volume_teu.{year}Q{quarter}"
    if inputs.has(key):
        return inputs.get(key), inputs.label(key), key
    for earlier in range(year - 1, year - 11, -1):
        source = f"freight.group_volume_teu.{earlier}Q{quarter}"
        if inputs.has(source):
            g = inputs.get("freight.growth_g")
            value = inputs.get(source) * (1 + g) ** (year - earlier)
            return value, A, f"{source} x (1 + g)^{year - earlier}"
    raise MissingProfileValue(key, "not_in_profile")


def freight_items(inputs: Inputs, start: date, end: date) -> list[ExposureItem]:
    items: list[ExposureItem] = []
    trades = list(Trade)
    h1 = {t: inputs.get(f"freight.h1_volume_teu.{t.value}") for t in trades}
    h1_total = sum(h1.values())
    g = inputs.get("freight.growth_g")

    by_quarter: dict[tuple[int, int], list[date]] = {}
    for month in months_between(start, end):
        by_quarter.setdefault((month.year, quarter_of(month)), []).append(month)

    for (year, q), months in by_quarter.items():
        def build(year=year, q=q, months=months) -> list[ExposureItem]:
            prior, prior_label, prior_src = quarter_volume(inputs, year - 1, q)
            out = []
            for trade in trades:
                share = h1[trade] / h1_total
                daily = prior * share * (1 + g) / days_in_quarter(year, q)
                for month in months:
                    days = overlap_days(month, start, end)
                    out.append(
                        _item(
                            OwnerSpec.FREIGHT, "volume", f"{trade.value}:{_ym(month)}",
                            ProvenanceLabel.weakest(prior_label, D, A),
                            f"Prior-year quarter volume ({prior_src}) x H1 2026 trade share x (1 + g), "
                            f"spread evenly over the days of {year}Q{q}.",
                            quantities={"teu": daily * days, "daily_teu": daily, "days": days},
                            dates={"month": month},
                        )
                    )
            return out

        items += inputs.attempt(f"freight volumes {year}Q{q}", build)

    contract = inputs.get("freight.contract_share")
    index = inputs.get("freight.index_linked_share")
    for trade in trades:
        p0 = inputs.get(f"freight.p0.{trade.value}")
        items.append(_item(OwnerSpec.FREIGHT, "spot", trade.value, A, "Spot share = 1 - contract share - index-linked share.",
                           currency=Currency.USD, quantities={"share": 1 - contract - index, "p0_usd_per_teu": p0}))
        items.append(_item(OwnerSpec.FREIGHT, "index", trade.value, A, "Index-linked share of trade volume.",
                           currency=Currency.USD, quantities={"share": index, "p0_usd_per_teu": p0}))
        scheme = inputs.get(f"freight.cohort_scheme.{trade.value}")
        if scheme == "annual_may":
            year = inputs.as_of.year if inputs.as_of < date(inputs.as_of.year, 5, 1) else inputs.as_of.year + 1
            resets = [(f"{trade.value}:annual", date(year, 5, 1), contract)]
        else:
            resets = [(f"{trade.value}:{_ym(d)}", d, contract / 12) for d in firsts_after(inputs.as_of, 12)]
        for key, reset, share in resets:
            items.append(_item(OwnerSpec.FREIGHT, "contract", key, A,
                               "Contract cohort at P0 until its reset (calendar ASSUMPTION, 6.3).",
                               currency=Currency.USD,
                               quantities={"share": share, "rate_usd_per_teu": p0, "term_months": 12},
                               dates={"reset_date": reset}))
    return items


# ------------------------------------------------------------------------ bunker (7.3)


def bunker_items(inputs: Inputs, start: date, end: date) -> list[ExposureItem]:
    items = []
    c0 = inputs.get("bunker.monthly_consumption_t")
    for month in months_between(start, end):
        first_day = max(month, start)
        w_sin = inputs.get("bunker.hub_weight_sin", first_day)
        scrubber = inputs.get("bunker.scrubber_share", first_day)
        fraction = overlap_days(month, start, end) / days_in_month(month)
        for hub, weight in (("SIN", w_sin), ("RTM", 1 - w_sin)):
            for grade, share in (("VLSFO", 1 - scrubber), ("HSFO", scrubber)):
                items.append(_item(
                    OwnerSpec.BUNKER, "purchase", f"{hub}:{grade}:{_ym(month)}", A,
                    "Monthly consumption C0 x hub weight x grade share (hub weights and scrubber share "
                    "ASSUMPTION), spread evenly over the days of the month.",
                    currency=Currency.USD,
                    quantities={"tonnes": c0 * weight * share * fraction},
                    dates={"month": month},
                ))
    return items


# ------------------------------------------------------------------------ rates (8.3)


def rates_items(inputs: Inputs, start: date, end: date, schedule: MaturitySchedule | None,
                cash_at_t0: float | None = None) -> list[ExposureItem]:
    items: list[ExposureItem] = []
    as_of = inputs.as_of
    variable = inputs.get("rates.variable_rate_debt_eur")
    split = inputs.get("rates.floating_split_usd")
    n = inputs.get("rates.floating_tranches_per_currency")

    def reset_dates(i: int) -> list[date]:
        return every_n_months(firsts_after(as_of, i)[-1], 3, end)

    def usd_floating() -> list[ExposureItem]:
        rate = inputs.get("fx.usd_per_eur_close_2025_12_31")
        notional = variable * rate * split / n
        return [
            _item(OwnerSpec.RATES, "float", f"USD:{i}", A,
                  "EUR 1,999.1m floating debt at the 31 Dec 2025 closing rate (D12) x USD split / tranches.",
                  currency=Currency.USD,
                  quantities={"notional": notional, "spread_bp": inputs.get("rates.spread_usd_bp"), "reset_months": 3},
                  dates={"resets": reset_dates(i)})
            for i in range(1, n + 1)
        ]

    items += inputs.attempt("USD floating tranches", usd_floating)
    for i in range(1, n + 1):
        items.append(_item(OwnerSpec.RATES, "float", f"EUR:{i}", A,
                           "EUR 1,999.1m floating debt x EUR split / tranches (native EUR, no conversion).",
                           currency=Currency.EUR,
                           quantities={"notional": variable * (1 - split) / n,
                                       "spread_bp": inputs.get("rates.spread_eur_bp"), "reset_months": 3},
                           dates={"resets": reset_dates(i)}))

    total = inputs.get("rates.financial_debt_total_eur")
    bond = inputs.get("rates.bond_notional_eur")
    coupon = inputs.get("rates.bond_coupon_pct") + (0.25 if inputs.get("rates.bond_step_up_applies") else 0)
    # One or more "MM-DD" coupon dates per year, separated by ";" (e.g. "04-15;10-15", semi-annual, P60).
    month_days = [tuple(int(x) for x in md.strip().split("-"))
                  for md in str(inputs.get("rates.bond_coupon_month_day")).split(";") if md.strip()]
    coupon_dates = sorted(date(y, mm, dd) for y in range(start.year, end.year + 1) for mm, dd in month_days
                          if start <= date(y, mm, dd) <= end)
    items.append(_item(OwnerSpec.RATES, "fixed", "EUR:bond", A,
                       "EUR 300m sustainability-linked bullet bond (AR p. 211); annual coupon_pct paid in "
                       "coupons_per_year equal parts on the coupon dates.",
                       currency=Currency.EUR,
                       quantities={"notional": bond, "coupon_pct": coupon, "coupons_per_year": len(month_days)},
                       dates={"coupons": coupon_dates}))

    month_ends = [month_end(m) for m in months_between(start, end) if month_end(m) >= start]

    def usd_fixed() -> list[ExposureItem]:
        rate = inputs.get("fx.usd_per_eur_close_2025_12_31")
        return [_item(OwnerSpec.RATES, "fixed", "USD:1", A,
                      "(Total financial debt - floating debt - EUR bond) at the 31 Dec 2025 closing rate; "
                      "fixed rate = disclosed weighted average (ASSUMPTION); monthly equal interest.",
                      currency=Currency.USD,
                      quantities={"notional": (total - variable - bond) * rate,
                                  "rate_pct": inputs.get("rates.fixed_rate_pct")},
                      dates={"payments": month_ends})]

    def lease() -> list[ExposureItem]:
        rate = inputs.get("fx.usd_per_eur_close_2025_12_31")
        return [_item(OwnerSpec.RATES, "lease", "USD:LEASE", D,
                      "FY2025 lease interest EUR 207.1m (AR p. 188) at the 31 Dec 2025 closing rate / 12 (D23).",
                      currency=Currency.USD,
                      quantities={"monthly_interest": inputs.get("rates.lease_interest_fy2025_eur") * rate / 12},
                      dates={"payments": month_ends})]

    items += inputs.attempt("USD fixed debt", usd_fixed)
    items += inputs.attempt("lease interest", lease)

    # P17 / P77: rolled-forward cash at T0 when the engine supplies it, else the last disclosed cash.
    cash = cash_at_t0 if cash_at_t0 is not None else inputs.get("liquidity.cash_and_equivalents_usd")
    cash_basis = "rolled-forward cash at T0 (DERIVED, P77)" if cash_at_t0 is not None else "cash USD 3,150m (30 Jun 2026)"
    bearing = cash * inputs.get("rates.interest_bearing_cash_share")
    usd_share = inputs.get("rates.deposit_split_usd")
    cohorts = inputs.get("rates.deposit_cohorts_per_currency")
    spread = inputs.get("rates.deposit_spread_bp")
    for currency, share in ((Currency.USD, usd_share), (Currency.EUR, 1 - usd_share)):
        for i in range(1, cohorts + 1):
            items.append(_item(
                OwnerSpec.RATES, "deposit", f"{currency.value}:{i}", A,
                f"{cash_basis} x interest-bearing share x currency split / cohorts. EUR balances are USD "
                "equivalents, converted at T0 by the module.",
                currency=currency,
                quantities={"balance_usd": bearing * share / cohorts, "spread_below_bp": spread, "roll_months": 3},
                dates={"rolls": every_n_months(firsts_after(as_of, i)[-1], 3, end)},
            ))

    zim_debt = inputs.get("rates.zim_amount_usd")
    # P63: the purchase consideration is paid at closing; profiles without the key (2026H1-v1) treat the
    # whole consideration as financed, so no cash leaves (the old D24 reading).
    consideration = inputs.get("rates.zim_consideration_usd") if inputs.has("rates.zim_consideration_usd") else zim_debt
    items.append(_item(OwnerSpec.RATES, "zim", "USD:1", A,
                       "ZIM acquisition: external financing (notional) and purchase consideration from the profile; "
                       "spread and draw date ASSUMPTION; state switch, default off (8.3, P63).",
                       currency=Currency.USD,
                       quantities={"notional": zim_debt, "consideration_usd": consideration,
                                   "spread_bp": inputs.get("rates.zim_spread_bp"),
                                   "switch_default": inputs.get("rates.zim_tranche_on")},
                       dates={"draw_date": inputs.get("rates.zim_draw_date")}))

    if schedule is not None:
        for s in schedule.slices:
            items.append(_item(
                OwnerSpec.RATES, "maturity", f"USD:{_ym(s.date)}", schedule.label,
                f"USD share of the single maturity schedule (1/12 of the 12-month amount; lease basis: "
                f"{schedule.lease_basis}); split pro rata floating/fixed (D44).",
                currency=Currency.USD,
                quantities={"amount": s.usd_part_usd, "floating_share": s.floating_share},
                dates={"maturity_date": s.date},
            ))
    return items


# ------------------------------------------------------------------------ fx (9B.3)


def fx_items(inputs: Inputs, start: date, end: date, schedule: MaturitySchedule | None) -> list[ExposureItem]:
    items: list[ExposureItem] = []
    fridays = weekdays_between(start, end, FRIDAY)
    cad = inputs.get("fx.cad_weekly_cost_cad")
    eur_weekly = inputs.get("fx.eur_operating_cost_per_quarter_eur") / 13
    for day in fridays:
        items.append(_item(OwnerSpec.FX, "opex", f"CAD:{day.isoformat()}", D,
                           "At least CAD 72m per 13 weeks from the CAD forward notional and 80% cap, weekly (9B.3).",
                           currency=Currency.CAD, quantities={"amount": cad}, dates={"payment_date": day}))
        items.append(_item(OwnerSpec.FX, "opex", f"EUR:{day.isoformat()}", A,
                           "EUR 350m per quarter (ASSUMPTION) / 13 weeks.",
                           currency=Currency.EUR, quantities={"amount": eur_weekly}, dates={"payment_date": day}))

    def dividend() -> list[ExposureItem]:
        when = inputs.get("fx.dividend_date")
        amount = inputs.get("fx.dividend_reference_eur")
        if not start <= when <= end:
            return []
        return [_item(OwnerSpec.FX, "dividend", f"EUR:{when.isoformat()}", A,
                      "Latest disclosed dividend as the FY2026 estimate; payment date ASSUMPTION (9B.3).",
                      currency=Currency.EUR, quantities={"amount": amount}, dates={"payment_date": when})]

    items += inputs.attempt("EUR dividend", dividend)

    shift = inputs.get("fx.obligation_shift_months")
    base_dates = [month_end(add_months(inputs.as_of.replace(day=1), i)) for i in range(1, 13)]
    for currency, key in ((Currency.INR, "fx.inr_obligation_inr"), (Currency.BRL, "fx.brl_obligation_brl")):
        monthly = inputs.get(key) / 12
        for i, base in enumerate(base_dates, start=1):
            items.append(_item(OwnerSpec.FX, "obligation", f"{currency.value}:{i}", A,
                               "Disclosed obligation (AR p. 215) in 12 equal monthly amounts on month ends; "
                               "dates ASSUMPTION; shifted by fx.obligation_shift_months.",
                               currency=currency, quantities={"amount": monthly},
                               dates={"payment_date": month_end(add_months(base, shift))}))

    for currency in (Currency.CNY, Currency.GBP):
        items.append(_item(OwnerSpec.FX, "memo", f"{currency.value}:1", D,
                           "Translation memo position from the AR p. 216 sensitivity; converted to native at T0 "
                           "by the module; never on the liquidity path (D19).",
                           currency=currency,
                           quantities={"position_usd": inputs.get(f"fx.memo_position_usd.{currency.value}")}))

    if schedule is not None:
        def eur_principal() -> list[ExposureItem]:
            rate = inputs.get("fx.usd_per_eur_close_2026_06_30")
            return [
                _item(OwnerSpec.FX, "maturity", f"EUR:{_ym(s.date)}", schedule.label,
                      "EUR share of the single maturity schedule, converted to EUR at the 30 Jun 2026 closing "
                      "rate of the disclosure (D12); split pro rata floating/fixed (D44).",
                      currency=Currency.EUR,
                      quantities={"amount": s.eur_part_usd / rate, "amount_usd_at_disclosure": s.eur_part_usd,
                                  "floating_share": s.floating_share},
                      dates={"maturity_date": s.date})
                for s in schedule.slices
            ]

        items += inputs.attempt("EUR debt principal", eur_principal)
    return items
