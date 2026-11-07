"""Regenerate the SYNTHETIC market fixtures deterministically (seed 20261031).

These are stand-ins for the spec's illustrative scenarios. They are not market data and
every value is tagged ILLUSTRATIVE by the provider. The last two observations of each series
reproduce the specification's tables (0.73; 2.00->2.25; 650->700; 1600->1728).
Usage: python scripts/build_fixtures.py
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src/treasury_sensitivity/fixtures"
END = date(2026, 10, 30)  # Friday
rng = random.Random(20261031)


def business_days_back(end: date, count: int) -> list[date]:
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def fridays_back(end: date, count: int) -> list[date]:
    return [end - timedelta(weeks=i) for i in range(count - 1, -1, -1)]


def walk_back(last: float, n: int, vol: float, digits: int) -> list[float]:
    """Random walk generated backwards so the final value is exactly `last`."""
    values = [last]
    for _ in range(n - 1):
        values.append(values[-1] / (1 + rng.gauss(0, vol)))
    return [round(v, digits) for v in reversed(values)]


META = {
    "synthetic": True,
    "warning": "SYNTHETIC FIXTURE - not market data. Tagged ILLUSTRATIVE by the provider.",
}


def series(series_id, name, unit, frequency, dates, values, **meta):
    return {series_id: {"series_id": series_id, "name": name, "unit": unit, "frequency": frequency,
                        "observations": [{"date": d.isoformat(), "value": v} for d, v in zip(dates, values)], **meta}}


out: dict = {"_meta": META, "series": {}}

# FX USD per CAD, business days, ends 0.73; a 6-business-day hole stays a hole (never interpolated)
d = business_days_back(END, 420)
v = walk_back(0.73, len(d), 0.0016, 4)
hole = set(range(200, 206))
d2 = [x for i, x in enumerate(d) if i not in hole]
v2 = [x for i, x in enumerate(v) if i not in hole]
out["series"].update(series("FX_CADUSD", "CAD/USD reference rate (USD per CAD)", "USD per CAD", "daily_business", d2, v2,
    currency="USD", source="Synthetic fixture (stands in for Frankfurter/ECB reference rates)", max_age_days=4,
    comparison_mode="one_week",
    caveat="Reference rate, not an executable price. Quote is USD per 1 CAD."))

def fx_series(series_id, name, unit, latest, week_ago):
    """Daily business-day walk ending at `latest`; the observation 5 business days earlier is pinned to `week_ago`."""
    d = business_days_back(END, 420)
    v = walk_back(latest, len(d), 0.0014, 6)
    v[-6] = week_ago
    out["series"].update(series(series_id, name, unit, "daily_business", d, v,
        currency="USD", source="Synthetic fixture (stands in for Frankfurter/ECB reference rates)", max_age_days=4,
        comparison_mode="one_week",
        caveat="Reference rate, not an executable price. Quote is " + unit + "."))


# Last two weekly points follow the supplied dashboard layout: USD/EUR 1.063 (-2.0%), USD/GBP 0.790 (+1.3%), USD/RMB 7.27 (-1.8%)
fx_series("FX_EURUSD", "EUR/USD reference rate (USD per EUR)", "USD per EUR", 1.063, round(1.063 / 0.98, 6))
fx_series("FX_GBPUSD", "GBP/USD reference rate (USD per GBP)", "USD per GBP", round(1 / 0.790, 6), round(1.013 / 0.790, 6))
fx_series("FX_CNYUSD", "CNY/USD reference rate (USD per CNY)", "USD per CNY", round(1 / 7.27, 6), round(0.982 / 7.27, 6))

# ECB deposit facility rate: step series 2.00 -> 2.25 on the last two business days
d = business_days_back(END, 420)
v = [2.00] * (len(d) - 2) + [2.25, 2.25]
out["series"].update(series("ECB_DFR", "ECB deposit facility rate", "% p.a.", "daily_business", d, v,
    currency="EUR", source="Synthetic fixture (stands in for ECB Data Portal FM.D.U2.EUR.4F.KR.DFR.LEV)", max_age_days=5,
    comparison_mode="previous_change",
    caveat="ECB policy benchmark. NOT Euribor and NOT the company borrowing rate."))

est = [round(2.00 - 0.07 + rng.gauss(0, 0.012), 3) for _ in d]
est[-2:] = [2.17, 2.18]
out["series"].update(series("ECB_ESTR", "Euro short-term rate (EUR STR)", "% p.a.", "daily_business", d, est,
    currency="EUR", source="Synthetic fixture (stands in for ECB Data Portal EST.B.EU000A2X2A25.WT)", max_age_days=5,
    comparison_mode="previous_observation",
    caveat="Overnight benchmark. NOT Euribor and NOT the company borrowing rate."))

# Rotterdam VLSFO 0.5%: weekly snapshots, 650 -> 700
d = fridays_back(END, 70)
v = walk_back(650.0, len(d) - 1, 0.025, 1) + [700.0]
v[-2] = 650.0
out["series"].update(series("VLSFO_ROTTERDAM", "Rotterdam VLSFO 0.5% (weekly snapshot)", "USD/t", "weekly", d, v,
    currency="USD", source="Synthetic fixture (Ship & Bunker / BunkerEx need API+licence confirmation)", max_age_days=9,
    comparison_mode="previous_observation",
    caveat="Market benchmark, NOT the company procurement price."))

# SCFI: weekly, 1600 -> 1728
d = fridays_back(END, 70)
v = walk_back(1600.0, len(d) - 1, 0.03, 1) + [1728.0]
v[-2] = 1600.0
out["series"].update(series("SCFI", "Shanghai Containerized Freight Index (SCFI, weekly)", "index points", "weekly", d, v,
    currency="USD", source="Synthetic fixture (SSE publication; no confirmed API)", max_age_days=9,
    comparison_mode="previous_observation",
    caveat="SCFI index, NOT the company realised freight rate. Do not mix with FBX."))

(OUT / "market.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

m13 = (END + timedelta(days=91)).isoformat()
m26 = (END + timedelta(days=182)).isoformat()
ASSUME = ("ASSUMED exposure, sized to the order of magnitude of the supplied layout. Hapag-Lloyd's H1 2026 report discloses "
          "only that the functional currency is USD and the net FX result is small (losses USD 237m, gains USD 244m).")
positions = [
    ("EXP-CAD-001", "CAD", "payable", "75", "CAD operating payables (synthetic)", m13, "CAD_PAYABLE_13W"),
    ("EXP-EUR-001", "EUR", "receivable", "3000", "EUR freight revenue net of EUR costs (synthetic)", m26, "EUR_RECEIVABLE_6M"),
    ("EXP-GBP-001", "GBP", "payable", "2000", "GBP operating payables (synthetic)", m26, "GBP_PAYABLE_6M"),
    ("EXP-CNY-001", "CNY", "payable", "15000", "CNY operating payables (synthetic)", m26, "CNY_PAYABLE_6M"),
]
hedges = [
    ("HDG-CAD-001", "CAD", "buy_forward", "57.5", positions[0][4], m13, "CAD_PAYABLE_13W", "DISCLOSED HISTORICAL",
     "Historical disclosed notional; not verified as a live matched hedge."),
    ("HDG-EUR-001", "EUR", "sell_forward", "1000", positions[1][4], m26, "EUR_RECEIVABLE_6M", "ILLUSTRATIVE", ASSUME),
    ("HDG-GBP-001", "GBP", "buy_forward", "500", positions[2][4], m26, "GBP_PAYABLE_6M", "ILLUSTRATIVE", ASSUME),
    ("HDG-CNY-001", "CNY", "buy_forward", "3000", positions[3][4], m26, "CNY_PAYABLE_6M", "ILLUSTRATIVE", ASSUME),
]
(OUT / "exposures.json").write_text(json.dumps({
    "_meta": {"synthetic": True, "warning": "SYNTHETIC exposures and hedges. No ERP/TMS/bank data. " + ASSUME},
    "as_of": END.isoformat(),
    "positions": [{"id": i, "currency": c, "direction": dr, "amount_m": a, "underlying": u, "maturity": m, "set": st,
                   "provenance": "ILLUSTRATIVE"} for i, c, dr, a, u, m, st in positions],
    "hedges": [{"id": i, "currency": c, "side": sd, "notional_m": n, "underlying": u, "maturity": m, "set": st,
                "provenance": pv, "verified": False, "contract_rate": None, "note": nt} for i, c, sd, n, u, m, st, pv, nt in hedges],
}, indent=1), encoding="utf-8")
print("fixtures written to", OUT)
