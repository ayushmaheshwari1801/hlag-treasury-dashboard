"""FX module (spec s4): dCF = -R x (S1 - S0) for a payable quoted USD per unit; receivables flip the sign."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from ..config import FIXTURE_DIR
from ..models import (D, ImpactType, InputSpec, Provenance, Quantity, TreasuryAction, ValidationProblem,
                      combine_provenance)
from .common import EngineContext, ScenarioRequest, build_result, horizon_scale, make_row, resolve_inputs

FORMULA = ("Payable: R = eligible gross exposure - matched eligible hedges; S1 = S0 x (1 + shock); "
           "dCF = -R x (S1 - S0). Receivable: dCF = +R x (S1 - S0). Quote = USD per 1 unit of foreign currency. "
           "Positive = favourable cash effect.")

SIDE_FOR = {"payable": "buy_forward", "receivable": "sell_forward"}


class Position(BaseModel):
    id: str
    currency: str
    direction: str
    amount_m: str
    underlying: str
    maturity: date
    set: str | None = None
    provenance: str = "ILLUSTRATIVE"


class Hedge(BaseModel):
    id: str
    currency: str
    side: str
    notional_m: str
    underlying: str
    maturity: date
    set: str | None = None
    provenance: str = "DISCLOSED HISTORICAL"
    verified: bool = False
    contract_rate: str | None = None
    note: str | None = None


class MatchResult(BaseModel):
    gross: Decimal
    matched: Decimal
    residual: Decimal
    over_hedged: Decimal
    eligible_hedge_ids: list[str]
    rejected: list[dict]
    warnings: list[str]
    unverified_hedges: list[str]


def load_positions(path: Path | None = None) -> tuple[list[Position], list[Hedge]]:
    raw = json.loads((path or FIXTURE_DIR / "exposures.json").read_text(encoding="utf-8"))
    return [Position(**p) for p in raw["positions"]], [Hedge(**h) for h in raw["hedges"]]


def match_positions(positions: list[Position], hedges: list[Hedge], *, currency: str, direction: str,
                    tolerance_days: int = 7) -> MatchResult:
    """Match currency, direction, underlying and maturity. Duplicates and unmatched items are reported, never silently used."""
    if direction not in SIDE_FOR:
        raise ValidationProblem("direction must be 'payable' or 'receivable'")
    warnings: list[str] = []
    rejected: list[dict] = []
    seen_pos: set[str] = set()
    exposures: list[tuple[Position, Decimal]] = []
    for p in positions:
        if p.id in seen_pos:
            warnings.append(f"Duplicate exposure id {p.id} ignored")
            continue
        seen_pos.add(p.id)
        if p.currency != currency or p.direction != direction:
            continue
        amount = D(p.amount_m)
        if amount <= 0:
            raise ValidationProblem(f"Exposure {p.id} must be positive")
        exposures.append((p, amount))
    exposures.sort(key=lambda t: t[0].maturity)

    seen_h: set[str] = set()
    pool: list[list] = []                               # [hedge, remaining]
    fingerprints: dict[tuple, str] = {}
    for h in hedges:
        if h.id in seen_h:
            rejected.append({"id": h.id, "reason": "duplicate hedge id"})
            continue
        seen_h.add(h.id)
        fp = (h.currency, h.side, h.notional_m, h.underlying.lower(), h.maturity)
        if fp in fingerprints:
            warnings.append(f"Hedge {h.id} has the same terms as {fingerprints[fp]}: possible duplicate, confirm before relying on it")
        fingerprints[fp] = h.id
        if h.currency != currency:
            continue
        if h.side != SIDE_FOR[direction]:
            rejected.append({"id": h.id, "reason": f"hedge side {h.side} does not offset a {direction}"})
            continue
        if D(h.notional_m) <= 0:
            rejected.append({"id": h.id, "reason": "non-positive notional"})
            continue
        pool.append([h, D(h.notional_m)])

    matched = Decimal(0)
    used_ids: set[str] = set()
    for p, amount in exposures:
        need = amount
        candidates = [e for e in pool if e[0].underlying.lower() == p.underlying.lower()
                      and abs((e[0].maturity - p.maturity).days) <= tolerance_days and e[1] > 0]
        candidates.sort(key=lambda e: abs((e[0].maturity - p.maturity).days))
        for entry in candidates:
            take = min(need, entry[1])
            if take > 0:
                entry[1] -= take
                need -= take
                matched += take
                used_ids.add(entry[0].id)
            if need == 0:
                break
    gross = sum((a for _, a in exposures), Decimal(0))
    over = Decimal(0)
    for h, remaining in pool:
        if h.id in used_ids:
            over += remaining
        elif remaining > 0:
            reason = "no exposure with the same underlying and maturity within tolerance"
            rejected.append({"id": h.id, "reason": reason})
    if over > 0:
        warnings.append(f"Over-hedged by {currency} {float(over):g}m versus matched exposure; excess ignored, not modelled as a reverse exposure")
    return MatchResult(gross=gross, matched=matched, residual=gross - matched, over_hedged=over,
                       eligible_hedge_ids=sorted(used_ids), rejected=rejected, warnings=warnings,
                       unverified_hedges=[h.id for h, _ in pool if h.id in used_ids and not h.verified])


def fx_cash_effect(residual: Decimal, spot: Decimal, shock_pct: Decimal, direction: str) -> Decimal:
    """USD m. S1 = S0 x (1 + shock%); payable: -R x (S1 - S0); receivable: +R x (S1 - S0)."""
    if spot <= 0:
        raise ValidationProblem("Spot must be positive")
    s1 = spot * (1 + shock_pct / 100)
    delta = residual * (s1 - spot)
    return -delta if direction == "payable" else delta


def compute(ctx: EngineContext, req: ScenarioRequest, positions: list[Position] | None = None,
            hedges: list[Hedge] | None = None):
    cfg = ctx.defaults["fx"]
    ccy = req.options.get("currency", cfg["default_currency"]).upper()
    if ccy not in cfg["currencies"]:
        raise ValidationProblem(f"currency must be one of {', '.join(cfg['currencies'])}")
    ccfg = cfg["currencies"][ccy]
    direction = (req.mode or ccfg["direction"]).lower()
    basis = req.options.get("basis", "selected")
    if basis not in ("selected", "observed_move"):
        raise ValidationProblem("basis must be 'selected' or 'observed_move'")
    positions = positions if positions is not None else ctx.positions
    hedges = hedges if hedges is not None else ctx.hedges
    if positions is None or hedges is None:
        positions, hedges = load_positions()
    sel = ccfg["position_set"]
    positions = [p for p in positions if p.set in (None, sel)]
    hedges = [h for h in hedges if h.set in (None, sel)]
    match = match_positions(positions, hedges, currency=ccy, direction=direction,
                            tolerance_days=cfg["maturity_tolerance_days"])
    market = ctx.blocks[ccfg["series"]]
    observed_pct = None if market.change_pct is None else D(market.change_pct)
    if basis == "observed_move":
        # Spot = previous comparable rate, shock = observed move: S1 - S0 equals latest - previous exactly.
        spot_default = D(market.previous_value) if market.previous_value is not None else None
        spot_note = "Previous comparable rate: the baseline the observed move is measured from."
    else:
        spot_default = D(market.latest_value) if market.latest_value is not None else None
        spot_note = "Latest reference rate, not an executable price."
    lo, hi = (D(x) for x in cfg["shock_bounds_pct"])
    scale, scale_note = horizon_scale(req.overrides, ccfg["horizon_days"])

    if "hedge_coverage" in req.overrides and "hedge_notional" in req.overrides:
        raise ValidationProblem("Set hedge_coverage or hedge_notional, not both")
    unverified = bool(match.unverified_hedges)
    specs = [
        InputSpec(key="gross_exposure", label=f"Eligible gross exposure ({direction})", unit=f"{ccy} m",
                  default=float(match.gross * scale) if match.gross else None, minimum=0, maximum=10_000_000, provenance=Provenance.ILLUSTRATIVE,
                  note="Synthetic position; replace with validated ERP/TMS data."),
        InputSpec(key="hedge_notional", label="Matched eligible hedge notional", unit=f"{ccy} m",
                  default=float(match.matched * scale), minimum=0, maximum=10_000_000,
                  provenance=Provenance.DISCLOSED_HISTORICAL if match.matched and ccy == "CAD" else Provenance.ILLUSTRATIVE,
                  note="Historical disclosed notional; not verified as a live matched hedge." if ccy == "CAD" and unverified else
                       ("Assumed hedge; replace with the live forward book." if unverified else None)),
        InputSpec(key="hedge_coverage", label="Hedge coverage (alternative to notional)", unit="fraction", default=None,
                  minimum=0, maximum=1.5, provenance=Provenance.ILLUSTRATIVE, note="If set, hedge = coverage x gross exposure."),
        InputSpec(key="spot", label=f"Spot S0 ({ccfg['quote']})", unit=ccfg["quote"],
                  default=float(spot_default) if spot_default is not None else None, minimum=0.0001, maximum=1000,
                  provenance=market.provenance, note=spot_note),
        InputSpec(key="horizon_days", label="Horizon", unit="days", default=ccfg["horizon_days"], minimum=1, maximum=730,
                  provenance=Provenance.ILLUSTRATIVE, note="Default 13 weeks for the CAD use case; six months otherwise."),
    ]
    values, shown = resolve_inputs(specs, req.overrides)
    gross, spot, horizon = values["gross_exposure"], values["spot"], int(values["horizon_days"])
    hedge = values["hedge_notional"]
    if values["hedge_coverage"] is not None:
        hedge = values["hedge_coverage"] * (gross or Decimal(0))
    warnings = list(match.warnings)
    if scale_note and "gross_exposure" not in req.overrides:
        warnings.append(scale_note)
    if match.rejected:
        warnings.append("Hedges not used: " + "; ".join(f"{r['id']} ({r['reason']})" for r in match.rejected))
    if unverified:
        warnings.append("Hedge(s) " + ", ".join(match.unverified_hedges) + (" are historical disclosed notionals, not verified live matched hedges"
                        if ccy == "CAD" else " are assumed notionals, not the live forward book"))
    if ccy != "CAD":
        warnings.append(f"{ccy} exposure is an ASSUMPTION: the H1 2026 report does not disclose {ccy} exposures or hedges")

    missing = []
    if gross is None or gross <= 0:
        missing.append("eligible gross exposure")
    if spot is None:
        missing.append("spot rate")
    residual = coverage = None
    if gross and gross > 0 and hedge is not None:
        matched = min(hedge, gross)
        if hedge > gross:
            warnings.append("Hedge exceeds gross exposure; matched amount capped at gross, excess not modelled")
        residual = gross - matched
        coverage = matched / gross
        if coverage > D(cfg["hedge_quota_cap"]):
            warnings.append(f"Coverage {float(coverage):.1%} exceeds the disclosed hedging quota of {D(cfg['hedge_quota_cap']):.0%}; confirm the policy basis")
    prov = combine_provenance(*(i.quantity.provenance for i in shown if i.key in ("gross_exposure", "hedge_notional", "spot")))
    if missing:
        prov = Provenance.UNAVAILABLE

    def impact_for(shock_pct: Decimal) -> Decimal | None:
        if missing or residual is None:
            return None
        return fx_cash_effect(residual, spot, shock_pct, direction)

    def check(s: Decimal) -> Decimal:
        if not lo <= s <= hi:
            raise ValidationProblem(f"FX shock {s}% is outside the allowed range {lo}% to {hi}%")
        return s

    if req.headline_shock is not None:
        head_shock = check(D(req.headline_shock))
    elif basis == "observed_move":
        if observed_pct is None:
            missing.append("observed one-week move")
            head_shock = Decimal(0)
        else:
            head_shock = check(observed_pct)
    else:
        head_shock = check(D(cfg["headline_shock_pct"]))
    grid = [(f"{float(D(s)):+g}% {ccy}", check(D(s))) for s in (req.shocks if req.shocks is not None else cfg["shock_grid_pct"])]

    def row(label: str, shock: Decimal):
        return make_row(ctx, req, label=label, shock=shock, shock_unit="%", impact=impact_for(shock), impact_unit="USD m",
                        currency="USD", places=3, comparable_to_liquidity=True, prov=prov, extra_missing=missing)

    rows = [row(label, s) for label, s in grid]
    tag = "observed 1-week move" if basis == "observed_move" and req.headline_shock is None else "selected"
    headline = row(f"{float(head_shock):+.2f}% {ccy} ({tag})" if tag != "selected" else f"{float(head_shock):+g}% {ccy} (selected)", head_shock)
    S0 = spot
    intermediate = [
        Quantity.of("Residual R", residual, f"{ccy} m", prov, places=3),
        Quantity.of("Hedge coverage", None if coverage is None else coverage * 100, "%", prov, places=2),
        Quantity.of("S1 at selected shock", None if spot is None else spot * (1 + head_shock / 100), ccfg["quote"], prov, places=5),
        Quantity.of("S1 - S0 at selected shock", None if S0 is None else S0 * head_shock / 100, ccfg["quote"], prov, places=5),
    ]
    others = [{"currency": c, "exposure_basis": "ILLUSTRATIVE assumption" if c != "CAD" else "ILLUSTRATIVE synthetic position",
               "how_to_view": {"options": {"currency": c}}} for c in cfg["currencies"] if c != ccy]
    disclosed = []
    for item in cfg["disclosed_earnings_sensitivities"]:
        for key, name in (("plus_10pct", "+10%"), ("minus_10pct", "-10%")):
            v = item[key]
            disclosed.append(Quantity.of(f"{item['currency']} earnings sensitivity {name} (FY2025, accounting, not cash)",
                                         None if v is None else D(v), item["unit"], Provenance.DISCLOSED_HISTORICAL, places=1,
                                         note=f"{item['source']}. Value not supplied in the specification attachments; populate after verification."))
    actions: list[TreasuryAction] = []
    if residual is not None and residual > 0:
        actions.append(TreasuryAction(rule_id="FX-A1", condition=f"If an unhedged {ccy} residual exists after eligibility matching",
                       action=f"Review whether the {ccy} residual should be covered, within the hedging policy quota and cost constraints.",
                       assumptions=["Residual uses an assumed or synthetic exposure"]))
    if unverified:
        actions.append(TreasuryAction(rule_id="FX-A2", condition="If the matched hedge is not a verified live hedge",
                       action="Validate hedge eligibility (currency, direction, underlying, maturity) and contracted forward rates before relying on the figure."))
    return build_result(
        ctx, req, module="fx", title=f"Foreign exchange ({ccy} {direction})", impact_type=ImpactType.FX_CASH, horizon_days=horizon,
        market_key=ccfg["series"], inputs=shown, intermediate=intermediate, formula=FORMULA,
        methodology=["1. Match exposures and hedges on currency, direction, underlying and maturity (tolerance "
                     f"{cfg['maturity_tolerance_days']} days); duplicates and unmatched hedges are reported.",
                     "2. R = gross exposure - matched eligible hedge (never below zero).",
                     "3. S1 = S0 x (1 + shock); dCF = -R x (S1 - S0) for a payable (sign reversed for a receivable)."
                     + (" With basis observed_move, S0 is the previous comparable rate and the shock is the observed move." if basis == "observed_move" else ""),
                     "4. Status uses adverse USD impact against comparable liquidity and any approved limit (materiality rules)."],
        headline=headline, rows=rows, disclosed_reference=disclosed, actions=actions, warnings=warnings,
        labels={"impact": f"Illustrative {ccy} FX cash-flow effect (USD m); positive = favourable", "quote": ccfg["quote"]},
        mode=direction, extra={"direction": direction, "currency": ccy, "basis": basis, "other_currencies": others,
                               "matching": match.model_dump(mode="json")},
        reliability_extra=["Exposure and hedge are synthetic, assumed or historical, not validated ERP/TMS data"])
