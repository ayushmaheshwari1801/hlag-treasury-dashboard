"""Methodology and breakdown for one driver (read-only; no calculation changes).

- breakdown(result): every paired line of a run assigned to exactly one component, dated, in USD. Driver
  components carry the native change at the base FX rate; the FX components carry the rest (the conversion
  effect on other modules' lines) and the full change of FX-owned lines. So per driver the components add up to
  the driver's attribution bar, and all rows together add up to the run's total delta. Cash beyond the engine
  horizon (the modules' watch lists) is reported separately, at the T0 rate.
- describe(driver, ...): the module's MethodologySpec with current values filled in: symbols, assumptions (profile,
  adjustment or per-run override), market inputs with plain source names, playbook entries and limitations.
- freight_composite(...): the H1-2026-volume-weighted route index used as the freight headline series.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from hlag_engine.company_profile.resolve import RunContext, resolve_detail
from hlag_engine.company_profile.schema import MissingProfileValue, Profile, ProfileError, Source
from hlag_engine.contracts.enums import LineCode, PassType, Trade
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.modules.bunker import BunkerModule
from hlag_engine.modules.bunker.methodology import SPEC as BUNKER
from hlag_engine.modules.freight import FreightModule
from hlag_engine.modules.freight.calc import INDEX_SERIES
from hlag_engine.modules.freight.methodology import SPEC as FREIGHT
from hlag_engine.modules.fx import FxModule
from hlag_engine.modules.fx.methodology import SPEC as FX
from hlag_engine.modules.methodology import APPROXIMATION_LABEL, Formula, MethodologySpec, Symbol, SymbolRef
from hlag_engine.modules.rates import RatesModule
from hlag_engine.modules.rates.methodology import SPEC as RATES

SPECS: dict[str, MethodologySpec] = {s.driver: s for s in (FREIGHT, BUNKER, RATES, FX)}
MODULES = {"freight": FreightModule, "bunker": BunkerModule, "rates": RatesModule, "fx": FxModule}
FX_CODES = ("EUR", "CAD", "GBP", "CNY", "INR", "BRL")
FUEL_SERIES = ("VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM")
ZERO = Decimal(0)

# Plain source names for the market series (licence wording for WCI and fuel pending decision P91).
SERIES_SOURCE = {
    **{f"FX_USD_PER_{c}": "ECB Data Portal (euro reference rates, as USD per unit)" for c in FX_CODES},
    "ESTR": "ECB Data Portal", "EA_2Y": "ECB Data Portal", "SOFR": "New York Fed Markets API",
    "UST_2Y": "FRED (St. Louis Fed), DGS2", "BB_OAS": "ICE BofA BB US High Yield OAS via FRED",
    "BRENT": "Brent crude (EIA) via FRED",
    **{s: "Drewry WCI via Bloomberg Terminal" for s in ("WCI_SHA_RTM", "WCI_SHA_LAX", "WCI_RTM_NYC", "WCI_COMPOSITE")},
    **{s: "ClearLynx bunker assessments via Bloomberg Terminal" for s in FUEL_SERIES},
}
SERIES_NAME = {
    "FX_USD_PER_EUR": "EUR / USD", "FX_USD_PER_CAD": "CAD / USD", "FX_USD_PER_GBP": "GBP / USD",
    "FX_USD_PER_CNY": "CNY / USD", "FX_USD_PER_INR": "INR / USD", "FX_USD_PER_BRL": "BRL / USD",
    "ESTR": "€STR", "SOFR": "SOFR", "UST_2Y": "US Treasury 2Y", "EA_2Y": "Euro area 2Y (AAA)",
    "BB_OAS": "BB high-yield OAS", "BRENT": "Brent crude",
    "WCI_SHA_RTM": "WCI Shanghai–Rotterdam", "WCI_SHA_LAX": "WCI Shanghai–Los Angeles",
    "WCI_RTM_NYC": "WCI Rotterdam–New York", "WCI_COMPOSITE": "WCI composite (Drewry)",
    "VLSFO_SIN": "VLSFO Singapore", "VLSFO_RTM": "VLSFO Rotterdam", "HSFO_SIN": "HSFO Singapore",
    "HSFO_RTM": "HSFO Rotterdam",
}
COMPANY_REPORTS = ("Investor Report", "Annual Report")


def source_name(source: Source | None) -> str | None:
    """'Investor Report H1 2026', page '10' -> 'Hapag-Lloyd Investor Report H1 2026, p. 10'."""
    if source is None:
        return None
    report = source.report
    if report.startswith(COMPANY_REPORTS):
        report = f"Hapag-Lloyd {report}"
    page = (source.page or "").strip()
    if not page or not any(ch.isdigit() for ch in page):
        return report  # 'webpage', 'release', 'official calendar'
    prefix = "pp." if any(ch in page for ch in ",-–") else "p."
    return f"{report}, {prefix} {page}"


# ---------------------------------------------------------------- breakdown


def _hedged_currency(sub_id: str, currency: str) -> str:
    """FX-owned USD hedge settlements belong to the currency they hedge (named in the sub_id)."""
    if currency != "USD":
        return currency
    named = [p for p in sub_id.split(":") if p in FX_CODES]
    return named[-1] if named else ("EUR" if "dividend" in sub_id else "OTHER")


def classify(owner: str, code: str, sub_id: str) -> tuple[str, str | None]:
    """(component, detail) of one line within its owner's row."""
    parts = sub_id.split(":")
    kind = parts[1] if len(parts) > 1 else ""
    if owner == "freight":
        return kind, parts[2] if len(parts) > 2 else None  # spot / index / contract / leaked, by trade
    if owner == "bunker":
        if code == LineCode.FUEL_PURCHASE:
            return "price_effect", None  # split further in breakdown()
        if code == LineCode.MFR_RECOVERY:
            return "mfr", parts[2] if len(parts) > 2 else None
        return ("hedge_settlement" if code == LineCode.HEDGE_SETTLEMENT else "margin"), None
    if owner == "rates":
        if code == LineCode.INTEREST_INCOME:
            return "cash_side", parts[2] if len(parts) > 2 else None
        if code in (LineCode.HEDGE_SETTLEMENT, LineCode.MARGIN_CALL):
            return "hedge", kind
        if code == LineCode.INTEREST_EXPENSE:
            detail = "bond" if sub_id.endswith(":bond") else kind
            return "debt_side", detail
        return "refinancing", {"refi": "new-issue interest", "maturity": "principal", "drawdown": "drawdown",
                               "zim": "ZIM"}.get(kind, kind)
    if owner == "fx":
        if code == LineCode.HEDGE_SETTLEMENT:
            return "policy_hedge", parts[2] if len(parts) > 2 else None
        return "own_exposure", kind
    raise ValueError(f"unknown owner {owner}")


def _purchase_month(sub_id: str) -> date | None:
    try:  # bunker:purchase:<hub>:<grade>:<YYYY-MM>
        y, m = sub_id.rsplit(":", 1)[1].split("-")
        return date(int(y), int(m), 1)
    except (ValueError, IndexError):
        return None


def _split_purchase(a: Decimal, b: Decimal, c_base: float | None, c_scen: float | None) -> tuple[Decimal, Decimal]:
    """(price effect, consumption effect) of one purchase line: they add up to b - a exactly."""
    if not c_base or not c_scen:
        return ZERO, b - a
    at_base_tonnes = b * Decimal(repr(c_base)) / Decimal(repr(c_scen))
    return at_base_tonnes - a, b - at_base_tonnes


def breakdown(result) -> dict[str, Any]:
    """Dated components of a run (USD, full units). items: one entry per (driver, component, detail, currency,
    date); beyond_horizon: native watch-list changes after the engine horizon, at the T0 base rate."""
    consumption = {pt: result.outputs[pt]["bunker"].diagnostics.get("monthly_consumption_t", {}) for pt in PassType}
    items: dict[tuple, Decimal] = defaultdict(lambda: ZERO)

    def add(driver, component, detail, currency, when, amount):
        if amount:
            items[(driver, component, detail, currency, when.isoformat())] += amount

    for pair in result.pairs:
        ref = pair.ref
        owner, code, ccy = ref.owner_spec.value, ref.line_code, ref.currency.value
        if owner == "fx":
            add("fx", *classify(owner, code, ref.sub_id), _hedged_currency(ref.sub_id, ccy), ref.date, pair.delta_usd)
            continue
        native = (pair.b - pair.a) * pair.x
        component, detail = classify(owner, code, ref.sub_id)
        if owner == "bunker" and code == LineCode.FUEL_PURCHASE:
            month = _purchase_month(ref.sub_id)
            price, volume = _split_purchase(pair.a, pair.b, consumption[PassType.BASE].get(month),
                                            consumption[PassType.SCENARIO].get(month))
            add(owner, "price_effect", None, ccy, ref.date, price * pair.x)
            add(owner, "consumption_effect", None, ccy, ref.date, volume * pair.x)
        else:
            add(owner, component, detail, ccy, ref.date, native)
        add("fx", "conversion", owner, ccy, ref.date, pair.delta_usd - native)

    rates = {c: Decimal(str(e.value)) for c in FX_CODES
             if (e := result.record.market_snapshot.get(f"FX_USD_PER_{c}")) is not None}
    rates["USD"] = Decimal(1)
    beyond: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    for pt, sign in ((PassType.SCENARIO, 1), (PassType.BASE, -1)):
        for module, out in result.outputs[pt].items():
            for w in out.watch_list:
                component, detail = classify(module, w.line_code, w.sub_id)
                amount = Decimal(repr(w.amount)) * rates.get(w.currency.value, ZERO) * sign
                beyond[(module, component, detail, w.currency.value)] += amount
    return {
        "unit": "USD",
        "items": [{"driver": k[0], "component": k[1], "detail": k[2], "currency": k[3], "date": k[4], "usd": v}
                  for k, v in sorted(items.items(), key=lambda kv: tuple(str(x) for x in kv[0]))],
        "beyond_horizon": [{"driver": k[0], "component": k[1], "detail": k[2], "currency": k[3], "usd": v}
                           for k, v in sorted(beyond.items(), key=lambda kv: tuple(str(x) for x in kv[0])) if v],
        "beyond_horizon_note": "Cash dated after the engine horizon (not on the liquidity path), native change at "
                               "the T0 exchange rate; fuel purchases there are not split into price and consumption.",
        "horizon_end": result.liquidity.paths.dates[-1].isoformat() if result.liquidity.paths.dates else None,
    }


# ---------------------------------------------------------------- describe


def _expand(key: str, driver: str) -> dict[str, str] | None:
    """Per-trade / per-series / per-currency keys -> {label: concrete key}; None for a single key."""
    if "{trade}" in key:
        return {t.value: key.replace("{trade}", t.value) for t in Trade}
    if "{index}" in key:
        return {t.value: INDEX_SERIES[t] for t in Trade}
    if "{fuel}" in key:
        return {s: s for s in FUEL_SERIES}
    if "{ccy}" in key:
        return {c: key.replace("{ccy}", c) for c in FX_CODES}
    return None


def _scenario_value(params: ScenarioParams | None, key: str):
    if params is None:
        return None
    group, name = key.split(".", 1)
    value = getattr(getattr(params, group), name, None)
    if isinstance(value, Mapping):
        return {str(getattr(k, "value", k)): v for k, v in value.items()}
    return getattr(value, "value", value)


def _symbol(sym: Symbol, driver: str, ctx: RunContext, profile: Profile, snapshot, params) -> dict:
    out = {"symbol": sym.symbol, "meaning": sym.meaning, "ref": sym.ref.value, "key": sym.key, "value": None,
           "unit": None, "source": None}
    if sym.ref is SymbolRef.NONE or sym.key is None:
        return out

    def one(key: str):
        if sym.ref is SymbolRef.PARAM:
            try:
                value = resolve_detail(key, ctx.as_of, ctx).value
            except (MissingProfileValue, ProfileError, KeyError):
                return None, None, None
            entry = profile.parameters[key]
            return value, entry.unit, source_name(entry.source) or entry.label.value
        if sym.ref is SymbolRef.SERIES:
            e = snapshot.get(key)
            return (float(e.value) if e else None), (e.unit if e else None), SERIES_SOURCE.get(key)
        if sym.ref is SymbolRef.ANCHOR:
            a = profile.anchor_snapshot.series.get(key)
            return (a.value if a else None), None, f"{profile.anchor_snapshot.period} average"
        return _scenario_value(params, key), None, "scenario"

    keys = _expand(sym.key, driver)
    if keys is None:
        out["value"], out["unit"], out["source"] = one(sym.key)
    else:
        values = {label: one(k) for label, k in keys.items()}
        out["value"] = {label: v[0] for label, v in values.items()}
        out["unit"] = next((v[1] for v in values.values() if v[1]), None)
        out["source"] = next((v[2] for v in values.values() if v[2]), None)
    return out


def _formula(f: Formula, *args) -> dict:
    return {"name": f.name, "expression": f.expression, "symbols": [_symbol(s, *args) for s in f.symbols]}


def assumptions(spec: MethodologySpec, ctx: RunContext, profile: Profile) -> list[dict]:
    """Each assumption the module uses: value as used in this run, its profile value and whether a per-run override
    or an approved adjustment changed it."""
    out = []
    for key, (name, used_for) in spec.used_for.items():
        entry = profile.parameters.get(key)
        if entry is None:
            continue
        base = entry.model_dump(mode="json")["value"]
        try:
            detail = resolve_detail(key, ctx.as_of, ctx)
            value, layer = detail.value, detail.layer
        except (MissingProfileValue, ProfileError):
            value, layer = None, "missing"
        if isinstance(value, date):
            value = value.isoformat()
        out.append({"key": key, "name": name, "used_for": used_for, "value": value, "unit": entry.unit,
                    "label": entry.label.value if layer == "profile" else "ASSUMPTION", "profile_label": entry.label.value,
                    "kind": entry.kind.value, "source": source_name(entry.source), "layer": layer,
                    "profile_value": base, "changed": layer in ("override", "adjustment") and value != base,
                    "overridable": entry.overridable, "status": entry.status.value})
    return out


def describe(driver: str, profile: Profile, ctx: RunContext, snapshot: Mapping[str, Any],
             params: ScenarioParams | None = None) -> dict[str, Any]:
    """The driver's methodology with current values. snapshot: series_id -> object with value, value_date, unit,
    label, confirmed, stale (the run's market snapshot)."""
    spec = SPECS[driver]
    module = MODULES[driver]()
    args = (driver, ctx, profile, snapshot, params)
    market = []
    for sid, role in spec.market_series.items():
        e = snapshot.get(sid)
        label = getattr(e, "label", None)
        market.append({"series_id": sid, "name": SERIES_NAME.get(sid, sid), "role": role,
                       "source": SERIES_SOURCE.get(sid), "value": float(e.value) if e else None,
                       "unit": getattr(e, "unit", None), "date": e.value_date.isoformat() if e else None,
                       "label": getattr(label, "value", label), "confirmed": bool(getattr(e, "confirmed", True)) if e else None,
                       "stale": bool(getattr(e, "stale", False)) if e else None})
    listed = assumptions(spec, ctx, profile)
    return {
        "driver": driver, "title": spec.title, "summary": list(spec.summary),
        "formulas": [_formula(f, *args) for f in spec.formulas],
        "approximation": {**_formula(spec.approximation, *args), "label": APPROXIMATION_LABEL},
        "components": [{"id": c.component_id, "label": c.label, "description": c.description, "detail": c.detail}
                       for c in spec.components],
        "assumptions": [a for a in listed if a["profile_label"] != "DISCLOSED"],
        "disclosed": [a for a in listed if a["profile_label"] == "DISCLOSED"],
        "market_inputs": market,
        "anchor_period": profile.anchor_snapshot.period,
        "playbook": [{"entry_id": p.entry_id, "trigger_type": p.trigger_type.value, "trigger": p.trigger,
                      "actions": list(p.actions), "hedge_tool": p.hedge_tool, "review_owner": p.review_owner}
                     for p in module.playbook()],
        "limitations": [*_module_notes(driver), *spec.limitations],
        "profile_version": profile.profile_version,
    }


def _module_notes(driver: str) -> list[str]:
    from importlib import import_module
    return list(import_module(f"hlag_engine.modules.{driver}.module").NOTES)


# ---------------------------------------------------------------- freight composite


def freight_composite(history: Mapping[str, Iterable[tuple[date, float]]], profile: Profile) -> list[tuple[date, float]]:
    """Σ_k w_k × I_k(d) on the dates every route index has a print, with w_k the H1 2026 trade volume shares
    (Africa & Intraregional on the Drewry composite, as in the calculation). USD/FEU."""
    weights = {t: float(profile.parameters[f"freight.h1_volume_teu.{t.value}"].value) for t in Trade}
    total = sum(weights.values())
    series = {t: dict(history.get(INDEX_SERIES[t], ())) for t in Trade}
    dates = set.intersection(*(set(s) for s in series.values())) if series else set()
    return [(d, sum(weights[t] / total * series[t][d] for t in Trade)) for d in sorted(dates)]


def composite_weights(profile: Profile) -> dict[str, float]:
    weights = {t.value: float(profile.parameters[f"freight.h1_volume_teu.{t.value}"].value) for t in Trade}
    total = sum(weights.values())
    return {t: w / total for t, w in weights.items()}
