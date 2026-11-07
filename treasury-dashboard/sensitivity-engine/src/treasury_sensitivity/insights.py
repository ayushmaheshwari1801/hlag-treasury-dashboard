"""Plain-language insights derived only from engine results, reported figures and the market series themselves.

Every sentence is assembled from numbers already in the dashboard payload; nothing is forecast or generated.
"""
from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from .models import D, ValidationProblem, quantize


def _f(x, places=1):
    if x is None:
        return None
    v = float(quantize(D(x), places))
    return 0.0 if v == 0 else v


def eur_text(v: float | None, places: int = 0) -> str:
    if v is None:
        return "n/a"
    s = f"{abs(v):,.{places}f}"
    return f"−€{s}m" if v < 0 else f"€{s}m"


# ------------------------------------------------------------------------------------------- unusual moves
def weekly_samples(series, weeks: int = 52):
    """Observations stepped back one week at a time from the latest (the last observation on or before each cutoff)."""
    obs = series.observations
    if not obs:
        return []
    out, cutoff, i = [], obs[-1].date, len(obs) - 1
    for _ in range(weeks + 1):
        while i >= 0 and obs[i].date > cutoff:
            i -= 1
        if i < 0:
            break
        out.append(obs[i])
        cutoff = obs[i].date - timedelta(days=7)
    return list(reversed(out))


def move_stats(series, *, in_bps: bool, weeks: int = 52) -> dict:
    """How unusual is the latest 1-week move against the past year of 1-week moves?"""
    s = weekly_samples(series, weeks)
    if len(s) < 9:
        return {"available": False, "reason": "Fewer than 8 weekly observations of history"}
    if in_bps:
        changes = [(b.value - a.value) * 100 for a, b in zip(s, s[1:])]
        unit = "bps"
    else:
        changes = [(b.value / a.value - 1) * 100 for a, b in zip(s, s[1:]) if a.value]
        unit = "%"
    latest, hist = changes[-1], changes[:-1]
    n = len(hist)
    mean = sum(hist) / n
    sd = math.sqrt(sum((c - mean) ** 2 for c in hist) / (n - 1)) if n > 1 else 0.0
    at_least = sum(1 for c in hist if abs(c) >= abs(latest) and latest != 0)
    if sd == 0:
        z = None
        label = "unusual" if latest != 0 else "normal"
        sigma_text = "no other weekly change in the past year" if latest != 0 else "unchanged, as in every week of the past year"
    else:
        z = latest / sd
        label = "unusual" if abs(z) >= 2 else "notable" if abs(z) >= 1 else "normal"
        sigma_text = f"{abs(z):.1f} standard deviations"
    return {"available": True, "latest_change": round(latest, 3), "unit": unit, "weeks": n, "sd": round(sd, 4),
            "z": None if z is None else round(z, 2), "times_as_large": at_least, "label": label,
            "text": (f"{sigma_text}; a move this large happened {at_least} time{'s' if at_least != 1 else ''} in the previous {n} weeks"
                     if latest != 0 and sd != 0 else sigma_text),
            "method": "Non-overlapping 1-week changes over the past year, from the same series; standard deviation of those changes."}


# --------------------------------------------------------------------------------------------- breakevens
def breakeven(row_id: str, label: str, unit: str, k_eur_per_unit: Decimal | None, remaining_eur: Decimal,
              bounds: tuple[float, float], base_level: float | None, level_kind: str, level_unit: str) -> dict:
    """Shock s at which this row alone uses up the remaining headroom: k*s = -remaining (impact is linear in s)."""
    out = {"id": row_id, "label": label, "unit": unit, "linear": True}
    if k_eur_per_unit is None or k_eur_per_unit == 0:
        return {**out, "available": False, "text": f"{label}: no exposure in the model, so it cannot use up headroom."}
    if remaining_eur <= 0:
        return {**out, "available": True, "shock": 0.0, "within_range": True, "direction": None,
                "text": f"{label}: headroom is already used up by the other modelled impacts."}
    s = -remaining_eur / k_eur_per_unit
    direction = "fall" if s < 0 else "rise"
    lo, hi = bounds
    within = lo <= float(s) <= hi
    level = None
    if base_level is not None:
        level = base_level + float(s) if level_kind == "add" else base_level * (1 + float(s) / 100) if level_kind == "pct" else None
    span = max(abs(lo), abs(hi))
    if abs(float(s)) > 3 * span:
        return {**out, "available": True, "shock": _f(s, 2), "direction": direction, "within_range": False, "level": None, "level_unit": level_unit,
                "text": f"{label}: would need a move more than three times the model's \u00b1{span:g}{'%' if unit == '%' else ' ' + unit} range; "
                        "not a realistic threat at the current exposure."}
    mag = f"{abs(float(s)):,.0f} bps" if unit == "bps" else f"${abs(float(s)):,.0f}/t" if unit == "USD/t" else f"{abs(float(s)):,.1f}%"
    lvl = "" if level is None or level <= 0 else f" (to about {level:,.4g} {level_unit})"
    text = (f"{label} would need to {direction} {mag} from last week{lvl} before headroom above the minimum is used up"
            + ("" if within else f", outside the model's ±{max(abs(lo), abs(hi)):g}{'%' if unit == '%' else ' ' + unit} range, so treat as remote") + ".")
    return {**out, "available": True, "shock": _f(s, 2), "direction": direction, "within_range": within,
            "level": None if level is None else round(level, 6), "level_unit": level_unit, "text": text}


# ------------------------------------------------------------------------------------------------- debt
def debt_insight(debt: dict, as_of, scenario_reserve_eur: float | None, headroom_eur: float | None) -> dict:
    years = [y for y in debt["years"] if y.isdigit()]
    totals = debt["totals"]
    if not years:
        return {"available": False}
    peak = max(years, key=lambda y: totals[y])
    y0 = str(as_of.year)
    # 12-month window from as_of: rest of this year's bucket (assumed still outstanding) + pro-rated next year
    months_next = as_of.month           # Nov 2026 -> 12 months ends Oct 2027: 10 months of 2027
    nxt = str(as_of.year + 1)
    next12 = (totals.get(y0, 0) if y0 in totals else 0) + (totals.get(nxt, 0) * (months_next - 0) / 12 if nxt in totals else 0)
    next12 = float(next12)
    cover = None if not scenario_reserve_eur or next12 <= 0 else scenario_reserve_eur / next12
    ok = cover is not None and cover >= 1
    peak_text = f"{peak} is the refinancing peak: {totals[peak]:,.0f} {debt['unit']} falls due, the largest single year before 2030."
    cover_text = (f"About {next12:,.0f} {debt['unit']} matures in the next 12 months. The scenario reserve of {scenario_reserve_eur:,.0f} covers it "
                  f"{cover:.1f}x" + ("" if ok else ", so refinancing is needed") + "." if cover is not None else "Coverage cannot be assessed.")
    after = None if scenario_reserve_eur is None else scenario_reserve_eur - next12
    return {"available": True, "peak_year": peak, "peak_amount": totals[peak], "next12_eur_m": round(next12), "coverage": None if cover is None else round(cover, 2),
            "covered": ok, "reserve_after_maturities_eur_m": None if after is None else round(after), "peak_text": peak_text, "cover_text": cover_text,
            "assumption": (f"Next-12-month amount = the whole {y0} bucket (assumed still outstanding) plus {months_next}/12 of {nxt}, "
                           "spread evenly. The profile itself is ILLUSTRATIVE; replace it via the debt_maturity data source."),
            "flag": "refinancing" if not ok else ("watch" if cover is not None and cover < 1.5 else None)}
