"""Validator for recommendation sets (recommendation-agent/3). Pure functions, no engine or network.

Each check returns exact, model-readable reasons so the agent can send them back for repair. An item that still
fails after the repairs is removed by the caller and listed under "removed by checks".

Rules (plan, checks 1-12): citations exist; every number in an item appears in the facts or tool results THAT ITEM
cites; events only by ticked event_id; company-specific names only from the facts or cited article text; statement
labels match what they cite; assumptions need a basis and only likely ones may support a decision; "trend
continues" (last week's moves repeat) only as a possible assumption in escalate_if; conflicting news inputs must be
stated as uncertainty; approval-safe phrasing with one of six business-function owners; hedge policy, including
bunker only when the engine marks fuel as exceptional; position consistent with the engine status.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .assistant import ISO_DATE, check_numbers
from .common_words import ordinary
from .fact_pack import CLOSED_KEYS, OWNER_ROLES, STATUS_ORDER

LABELS = ("Fact", "Engine", "News forecast", "AI assumption", "To verify")
POSITIONS = ("business_as_usual", "heightened_monitoring", "defensive", "opportunistic")
TRIGGERS = ("Occurred", "Forecast", "Structural")
VERBS = ("Prepare", "Review", "Propose", "Monitor", "Confirm", "Defer", "Pre-arrange", "Escalate", "Assess",
         "Reconcile", "Request", "Maintain", "Set", "Agree", "Brief", "Hold", "Increase", "Reduce", "Accelerate",
         "Raise")
# Relative timing ("within 2 weeks", "in the next 30 days"): its number is a duration, not a figure from the facts.
RELATIVE_TIMING = re.compile(r"\b(?:within|in)\s+(?:the\s+next\s+)?\d+\s+(?:business\s+|working\s+)?"
                             r"(?:day|week|month|quarter)s?\b", re.I)
# Facts that size what is at stake (cash amounts or headroom shares of the baseline / situation).
STAKE_PREFIX = ("F.sit.", "F.der.", "F.liq.", "F.pay.", "F.eng.market_moves_7d")
RECOMMENDABLE = {"resolves_breach", "material", "marginal"}
EXECUTION = re.compile(r"\b(execute|place (?:the|a) trade|buy|sell|enter into|draw down|draw on|transfer|pay out|"
                       r"book (?:the|a) hedge)\b", re.I)
# Amounts in USD millions in user-facing text ("2743.6 USD m", "USD 725m"); rates and prices (USD per EUR, USD/t) are fine.
USD_AMOUNT = re.compile(r"\bUSD\s*[\d,.]+\s*(?:m|mn|million|bn|billion)\b|[\d,.]+\s*USD\s*(?:m|mn|million|bn|billion)\b", re.I)
UNCERTAINTY = re.compile(r"\b(conflict\w*|disagree\w*|opposite|uncertain\w*|diverg\w*)\b", re.I)
HEDGE_WORD = re.compile(r"\bhedg", re.I)
TREND = "F.eng.trend_continues"
# Generic words that make the preceding word an institution name ("Deutsche Bank", "Acme Group").
INSTITUTION = {"bank", "banks", "group", "company", "holding", "holdings", "fund", "capital", "partners", "trust",
               "insurance", "securities", "facility", "facilities", "board", "committee", "management"}
FACT_LABEL_PROV = {"DISCLOSED", "MARKET", "DERIVED", "ENGINE", "PROXY"}
TRIGGER_PREFIX = {"Occurred": ("F.mkt.", "F.alert.", "F.occ.", "F.eng.market_moves_7d"), "Forecast": ("F.news.", "F.sit."),
                  "Structural": ("F.liq.", "F.pay.", "F.policy.", "F.bank.", "F.horizon.", "F.sit.")}
# Capitalised words that are not company-specific names (sentence starters, months, terms, units).
GENERIC = {w.casefold() for w in """
A An The This That These Those If When While Until Before After During Within Without With For From To In On At By Of
And Or But Not No Yes Any All Each Every Both Either Neither Other Another Such Only Also Then Than As Is Are Be
Prepare Review Propose Monitor Confirm Defer Pre-arrange Escalate Assess Reconcile Request Maintain Set Agree Brief
Consider Keep Hold Track Check Use Treat Note Expected Effect Owner Timing Trigger Why Escalate Usual Practice
January February March April May June July August September October November December Jan Feb Mar Apr Jun Jul Aug
Sep Sept Oct Nov Dec Monday Tuesday Wednesday Thursday Friday Q1 Q2 Q3 Q4 H1 H2 FY T0 T1 T2 T3 T4
EUR USD GBP CNY CAD INR BRL FX VLSFO HSFO WCI FEU TEU ESTR SOFR OAS BB EBITDA OCF MFR BAF CSA ISDA KPI AI
LOW WATCH HIGH CRITICAL Fact Engine News Forecast Assumption Verify To-verify Occurred Structural Situation Baseline
Treasury CFO Credit Control Procurement Commercial Pricing Commercial/Pricing Risk Committee Business Function
Headroom Liquidity Reserve Minimum Cash Bunker Fuel Freight Rates Interest Dividend Debt Capex Lease Policy Hedge
Swap Forward Option Ratio Cap Limit Board Management Company Group Bank Banks Counterparty Facility Facilities
Event Events Article Articles Source Sources Market Markets Scenario Run Path Status Position Decision
""".split()}


# ------------------------------------------------------------------------------------------------ helpers
def _walk(obj: Any):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


def _texts(item: Any) -> list[str]:
    """Every user-facing text of an item (statement texts and plain strings), not ids or cites."""
    out = []
    if isinstance(item, dict):
        for k, v in item.items():
            if k in {"cites", "event_ids", "assumption_ids", "to_verify_ids", "candidate_id", "id", "basis",
                     "decision_id", "label", "kind", "likelihood", "effort", "reversibility", "type", "rank", "role",
                     "verb"}:  # verb: checked against the fixed verb list instead
                continue
            out += _texts(v)
    elif isinstance(item, list):
        for v in item:
            out += _texts(v)
    elif isinstance(item, str):
        out.append(item)
    return out


def _cites(item: Any) -> list[str]:
    out = []
    if isinstance(item, dict):
        for k, v in item.items():
            if k in {"cites", "basis"} and isinstance(v, list):
                out += [c for c in v if isinstance(c, str)]
            else:
                out += _cites(v)
    elif isinstance(item, list):
        for v in item:
            out += _cites(v)
    return out


def _statements(item: dict, *fields: str) -> list[tuple[str, dict]]:
    """(field, statement) pairs; a statement is {text, cites[, label]}."""
    out = []
    for f in fields:
        v = item.get(f)
        if isinstance(v, dict):
            out.append((f, v))
        elif isinstance(v, list):
            out += [(f"{f}[{i}]", s) for i, s in enumerate(v) if isinstance(s, dict)]
    return out


class Context:
    """What an item may cite: the fact pack, this session's tool results, the set's assumptions and checks."""

    def __init__(self, pack: dict, tool_results: dict[str, dict] | None = None,
                 assumptions: list[dict] | None = None, to_verify: list[dict] | None = None):
        self.pack, self.facts = pack, pack["facts"]
        self.tools = dict(tool_results or {})
        self.assumptions = {a["id"]: a for a in assumptions or [] if isinstance(a, dict) and "id" in a}
        self.to_verify = {v["id"]: v for v in to_verify or [] if isinstance(v, dict) and "id" in v}
        self.ticked = set(pack.get("ticked", []))
        corpus = [pack.get("article_text", ""), " ".join(pack.get("names", [])),
                  json.dumps(self.facts, ensure_ascii=False, default=str)]
        self.corpus = " ".join(corpus).casefold()
        self.conflicts = {c["input"] for c in pack.get("conflicts", [])}

    def has(self, phrase: str) -> bool:
        """Whole-word, case-insensitive match in the facts, names and cited article text."""
        return re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", self.corpus) is not None

    def known(self, cid: str) -> bool:
        return cid in self.facts or cid in self.tools or cid in self.assumptions or cid in self.to_verify

    def cited_objects(self, cites: list[str]) -> list[Any]:
        out = []
        for c in cites:
            if c in self.facts:
                out.append(self.facts[c])
            elif c in self.tools:
                out.append(self.tools[c])
            elif c in self.assumptions:  # an assumption brings its basis facts
                out += [self.facts[b] for b in self.assumptions[c].get("basis", []) if b in self.facts]
        return out


# --------------------------------------------------------------------------------------------- the checks
def check_citations(item: Any, ctx: Context) -> list[str]:
    bad = sorted({c for c in _cites(item) if not ctx.known(c)})
    return [f"unknown citation {c}: cite only fact ids, tool result ids, assumption ids or to-verify ids" for c in bad]


def check_numbers_cited(item: Any, ctx: Context, where: str = "this item") -> list[str]:
    """Every number in the item's texts must be in the facts or tool results that the item cites (not the session)."""
    texts = _texts(item)
    cited = ctx.cited_objects(sorted(set(_cites(item))))
    problems = []
    text = " ".join(texts)
    for tok in check_numbers(text, {"cited": cited}):
        shown = ", ".join(f"{f.get('id')} = {f.get('value')} {f.get('unit') or ''}".strip() +
                          (f" / {f['eur_value']} EUR m" if f.get("eur_value") is not None else "")
                          for f in cited if isinstance(f, dict) and "id" in f)[:300]
        problems.append(f"number {tok} in {where} is not in the facts this item cites" + (f" ({shown})" if shown else ""))
    allowed_dates = {d for o in cited for v in _walk(o) if isinstance(v, str) for d in ISO_DATE.findall(v)}
    for d in sorted(set(ISO_DATE.findall(text)) - allowed_dates):
        problems.append(f"date {d} in {where} is not in the facts this item cites")
    return problems


def _plain_word(word: str) -> bool:
    """An ordinary English word or generic term, including hyphenated compounds of them ("Engine-tested")."""
    return all(ordinary(part) or part.casefold() in GENERIC for part in re.split(r"[-/]", word) if part)


def _known(word: str, ctx: "Context") -> bool:
    """A capitalised word is known when it appears in the facts, the allow-list or the cited article text; a hyphenated
    word ("VLSFO-versus-Brent", "Asia-Europe") is known when every part is an ordinary word, a generic term or in the
    facts."""
    if ctx.has(word):
        return True
    parts = [x for x in re.split(r"[-–/]", word) if x]
    return len(parts) > 1 and all(ordinary(x) or x.casefold() in GENERIC or ctx.has(x) for x in parts)


NAME = re.compile(r"(?:[A-Z][\w&'’\-]*(?:\s+(?:of|de|der|von|&)?\s*[A-Z][\w&'’\-]*)*)")


def check_names(item: Any, ctx: Context) -> list[str]:
    """Capitalised names must appear in the facts, the allow-list or the cited article text."""
    problems = []
    for text in _texts(item):
        for sentence in re.split(r"(?<=[.!?;:])\s+|\n", text):
            for m in NAME.finditer(sentence):
                phrase = m.group(0).strip()
                words = phrase.split()
                if m.start() == 0 and _plain_word(words[0]):
                    # Sentence start: an ordinary English word is just capitalised ("Review the reserve"); any other
                    # word is checked like a name ("Maersk raised its rates"). A frequent name followed only by an
                    # institution word ("Deutsche Bank") is still checked as a whole.
                    rest = words[1:]
                    if not (rest and all(w.casefold() in GENERIC for w in rest)
                            and any(w.casefold() in INSTITUTION for w in rest)):
                        words = rest
                if not words:
                    continue
                words_left = [w for w in words if w.casefold() not in GENERIC]
                if not words_left:
                    continue
                candidate = " ".join(words)
                if ctx.has(candidate) or all(_known(w, ctx) for w in words_left):
                    continue
                problems.append(f"name '{candidate}' is not in the facts or cited articles; company-specific names "
                                "must come from the facts")
    return sorted(set(problems))


def check_labels(stmts: list[tuple[str, dict]], ctx: Context) -> list[str]:
    problems = []
    for where, s in stmts:
        label, cites = s.get("label"), [c for c in s.get("cites", []) if isinstance(c, str)]
        if label is None:
            continue
        if label not in LABELS:
            problems.append(f"{where}: label must be one of {', '.join(LABELS)}")
        elif label in ("Fact", "Engine") and not any(
                (c in ctx.facts and ctx.facts[c]["provenance"] in FACT_LABEL_PROV) or c in ctx.tools for c in cites):
            problems.append(f"{where}: a {label} statement must cite a disclosed, market, derived or engine fact")
        elif label == "News forecast" and not any(c.startswith("F.news.") and c in ctx.facts for c in cites):
            problems.append(f"{where}: a News forecast statement must cite a News fact")
        elif label == "AI assumption" and not any(c in ctx.assumptions for c in cites):
            problems.append(f"{where}: an AI assumption statement must cite an assumption id")
        elif label == "To verify" and not any(c in ctx.to_verify for c in cites):
            problems.append(f"{where}: a To verify statement must cite a to-verify id")
    return problems


def check_events(item: dict, ctx: Context) -> list[str]:
    problems = [f"event {e} is not a ticked Event" for e in item.get("event_ids", []) if e not in ctx.ticked]
    for c in _cites(item):
        m = re.match(r"F\.news\.([^.]+)", c)
        if m and m.group(1) != "conflict" and m.group(1) not in ctx.ticked:
            problems.append(f"event {m.group(1)} is not a ticked Event")
    return sorted(set(problems))


def check_assumption(a: dict, ctx: Context) -> list[str]:
    problems = []
    if not isinstance(a.get("statement"), str) or not a["statement"].strip():
        problems.append("assumption needs a statement")
    basis = a.get("basis")
    if not isinstance(basis, list) or not basis:
        problems.append("assumption needs a basis: cite the fact ids it rests on")
    else:
        unknown = [b for b in basis if b not in ctx.facts]
        problems += [f"assumption basis {b} is not a fact id" for b in unknown]
    if a.get("likelihood") not in ("likely", "possible"):
        problems.append("assumption likelihood must be likely or possible")
    if a.get("likelihood") == "likely" and any(str(b).startswith(TREND) for b in basis or []):
        problems.append(f"{TREND} is the 'last week's moves repeat' assumption: an assumption based on it must be "
                        "possible, used only in escalate_if")
    if a.get("likelihood") == "likely":
        for b in basis or []:
            m = re.match(r"F\.mkt\.(.+)\.chg_(1w|4w)$", str(b))
            if not m:
                continue
            sid = m.group(1)
            moves = [ctx.facts.get(f"F.mkt.{sid}.chg_{w}", {}).get("value") for w in ("1w", "4w")]
            if not all(isinstance(v, (int, float)) and v for v in moves) or (moves[0] > 0) != (moves[1] > 0):
                problems.append(f"{sid}: its 1-week and 4-week moves do not have the same direction, so a trend based "
                                "on it is possible, not likely")
    if not isinstance(a.get("effect_if_wrong"), str) or not a["effect_if_wrong"].strip():
        problems.append("assumption needs effect_if_wrong")
    probe = {"text": a.get("statement", ""), "cites": basis or []}
    problems += check_names(probe, ctx) + check_numbers_cited(probe, ctx, "the assumption")
    problems += check_events({"event_ids": [], "cites": basis or []}, ctx)
    return problems


def check_decision(d: dict, ctx: Context) -> list[str]:
    p: list[str] = []
    for f in ("verb", "scope"):
        if not isinstance(d.get(f), str) or not d[f].strip():
            p.append(f"{f} is required (verb + scope + amount or limit + timing + owner)")
    if isinstance(d.get("verb"), str) and d["verb"].strip() not in VERBS:
        p.append(f"verb must be one of {', '.join(VERBS)} (recommendations for approval, never execution)")
    for f in ("amount_or_limit", "timing"):
        if not isinstance(d.get(f), dict) or not str(d[f].get("text", "")).strip():
            p.append(f"{f} is required; write \"no limit set\" or a dated condition if there is none")
    stake = d.get("cash_at_stake")
    if not isinstance(stake, dict) or not str(stake.get("text", "")).strip() or not stake.get("cites"):
        p.append("cash_at_stake is required: a statement citing the facts that size what is at stake (e.g. the "
                 "driver's F.sit.row.* effect, F.sit.headroom_change_vs_base or F.der.* headroom consumed)")
    elif not any(str(c).startswith(STAKE_PREFIX) and not str(c).startswith(TREND)
                 and ctx.facts.get(c, {}).get("unit") in ("USD m", "%") for c in stake["cites"]):
        p.append("cash_at_stake must cite a cash or headroom fact (USD m or %) from F.sit.*, F.der.*, F.liq.*, F.pay.* "
                 "or F.eng.market_moves_7d*")
    role = (d.get("owner") or {}).get("role") if isinstance(d.get("owner"), dict) else d.get("owner")
    if role not in OWNER_ROLES:
        p.append(f"owner '{role}' is not one of the six business functions: {', '.join(OWNER_ROLES)}")
    trig = d.get("trigger") or {}
    if trig.get("type") not in TRIGGERS:
        p.append("trigger type must be Occurred, Forecast or Structural")
    elif not any(c.startswith(TRIGGER_PREFIX[trig["type"]]) for c in trig.get("cites", [])):
        p.append(f"a {trig['type']} trigger must cite a fact starting with {' or '.join(TRIGGER_PREFIX[trig['type']])}")
    text = " ".join(_texts(d))
    if USD_AMOUNT.search(text):
        p.append("write amounts in EUR m (each fact's eur_value), not USD m; the rate is F.fx.t0_usd_per_eur")
    if EXECUTION.search(text):
        p.append("decisions are recommendations for approval: do not tell anyone to execute, trade, buy, sell, draw "
                 "or pay")
    # citations, numbers, names, labels, events
    numbered = d
    if isinstance(d.get("timing"), dict):  # relative timing: its number is a duration, not a figure
        numbered = {**d, "timing": {**d["timing"], "text": RELATIVE_TIMING.sub(" ", str(d["timing"].get("text", "")))}}
    p += check_citations(d, ctx) + check_numbers_cited(numbered, ctx) + check_names(d, ctx) + check_events(d, ctx)
    stmts = _statements(d, "why", "if_wrong", "escalate_if", "expected_effect", "amount_or_limit", "timing", "trigger",
                        "cash_at_stake")
    p += check_labels(stmts, ctx)
    # assumptions supporting the decision must be likely; trend facts only in escalate_if
    for aid in d.get("assumption_ids", []):
        a = ctx.assumptions.get(aid)
        if a is None:
            p.append(f"unknown assumption {aid}")
        elif a.get("likelihood") != "likely":
            p.append(f"assumption {aid} is possible: possible assumptions may appear only in escalate_if")
    for where, s in stmts:
        if where.startswith("escalate_if"):
            continue
        for c in s.get("cites", []):
            if str(c).startswith(TREND):
                p.append(f"{where}: {TREND} is the 'last week's moves repeat' assumption: use it only as a "
                         "possible assumption in escalate_if")
            a = ctx.assumptions.get(c)
            if a is not None and a.get("likelihood") != "likely":
                p.append(f"{where}: assumption {c} is possible; use it only in escalate_if")
            f = ctx.facts.get(c)
            if f is not None and f.get("used") is False:
                p.append(f"{where}: {c} is an overlapping news input that was not added; it cannot support a decision")
    for i, s in enumerate(d.get("escalate_if", []) or []):
        if not isinstance(s, dict) or not s.get("cites"):
            p.append(f"escalate_if[{i}] must cite existing facts (a threshold on a fact or a dated item)")
    # conflicting news inputs must be stated as uncertainty
    cited = set(_cites(d))
    for c in cited:
        f = ctx.facts.get(c)
        if not f or "param_key" not in f:
            continue
        slot = c.split(".input.", 1)[1] if ".input." in c else None
        if slot in ctx.conflicts:
            conflict = f"F.news.conflict.{slot}"
            states = [s for w, s in _statements(d, "why", "if_wrong") if conflict in s.get("cites", [])
                      and UNCERTAINTY.search(str(s.get("text", "")))]
            if not states:
                ev = next(x for x in ctx.pack["conflicts"] if x["input"] == slot)["events"]
                p.append(f"decision relies on {slot}, where Events {' and '.join(e['event_id'] for e in ev)} conflict; "
                         f"cite {conflict} in why or if_wrong and state the uncertainty")
    # hedges
    cand_id = (d.get("expected_effect") or {}).get("candidate_id")
    is_hedge = d.get("kind") == "hedge" or bool(HEDGE_WORD.search(f"{d.get('verb', '')} {d.get('scope', '')}"))
    if is_hedge:
        p += check_hedge(d, cand_id, ctx, cited)
    elif cand_id is None and (d.get("expected_effect") or {}).get("text", "not quantified").strip() != "not quantified":
        p.append("expected_effect must be \"not quantified\" unless it cites a tested candidate")
    return sorted(set(p), key=p.index)


def check_hedge(d: dict, cand_id: str | None, ctx: Context, cited: set[str]) -> list[str]:
    cand = ctx.tools.get(cand_id) if cand_id else None
    if cand is None:
        return ["a hedge decision must cite a tested candidate (expected_effect.candidate_id); otherwise list it as a "
                "rejected alternative"]
    p = []
    if cand.get("grade") not in RECOMMENDABLE:
        p.append(f"candidate {cand_id} has grade {cand.get('grade')} and cannot be a decision")
    module, spec = cand.get("module"), cand.get("spec") or {}
    if module == "fx":
        key = spec.get("key", "")
        name = key.removeprefix("fx.hedge_ratio.")
        policy = ctx.facts.get(f"F.policy.{name}", {})
        if key in CLOSED_KEYS:
            p.append(f"{key} is closed by policy (EUR operating costs and the dividend): list it as a rejected alternative")
        elif policy.get("room_pct_points") == 0:
            p.append(f"{key} has no room under its cap: list it as a rejected alternative")
        if f"F.policy.{name}" not in cited:
            p.append(f"a FX hedge decision must cite F.policy.{name}")
    if module == "bunker" and ctx.facts.get("F.sit.bunker_driver", {}).get("value") == "consumption":
        p.append("fuel cash moves through consumption, not price (F.sit.bunker_driver), so a fuel price hedge cannot "
                 "offset it: propose an operations review (routing, speed, consumption, surcharge recovery) instead")
    if module == "bunker":
        flag = ctx.facts.get("F.eng.bunker_exceptional", {})
        if flag.get("value") is not True:
            p.append("bunker hedge needs an engine exception (status HIGH/CRITICAL or a bunker desk exception); "
                     "F.eng.bunker_exceptional is false: move it to rejected alternatives")
        elif not {"F.eng.bunker_exceptional", "F.policy.bunker"} <= cited:
            p.append("a bunker hedge decision must cite F.eng.bunker_exceptional and F.policy.bunker")
    return p


def check_alternative(a: dict, ctx: Context) -> list[str]:
    p = []
    if not isinstance(a.get("title"), str) or not a["title"].strip():
        p.append("rejected alternative needs a title")
    if not isinstance(a.get("reason"), dict) or not str(a["reason"].get("text", "")).strip():
        p.append("rejected alternative needs a reason")
    if a.get("candidate_id") and a["candidate_id"] not in ctx.tools:
        p.append(f"unknown candidate_id {a['candidate_id']}")
    probe = {**a, "cites": list({*_cites(a), *([a["candidate_id"]] if a.get("candidate_id") else [])})}
    return p + check_citations(a, ctx) + check_numbers_cited(probe, ctx, "the alternative") + check_names(a, ctx)


def check_to_verify(v: dict, ctx: Context) -> list[str]:
    p = []
    if not isinstance(v.get("check"), str) or not v["check"].strip():
        p.append("to-verify item needs a check")
    return p + check_names(v, ctx)


def check_position(pos: dict, ctx: Context) -> list[str]:
    p = []
    value = pos.get("value")
    if value not in POSITIONS:
        return [f"position must be one of {', '.join(POSITIONS)}"]
    headline = pos.get("headline")
    if not isinstance(headline, list) or len(headline) != 2 or not all(isinstance(s, str) and s.strip() for s in headline):
        p.append("headline must be exactly two sentences")
    cites = pos.get("cites", [])
    if any(str(c).startswith(TREND) for c in cites):
        p.append(f"the position may not rest on {TREND} (an assumption, not an outcome)")
    statuses = [ctx.facts.get(k, {}).get("value") for k in ("F.liq.base.status", "F.sit.status")]
    breach = any(ctx.facts.get(k, {}).get("value") not in (None, "none") for k in ("F.liq.base.first_breach", "F.sit.first_breach"))
    worst = max((STATUS_ORDER.get(s, 0) for s in statuses if s), default=0)
    if value == "business_as_usual" and (worst >= STATUS_ORDER["HIGH"] or breach):
        p.append("business_as_usual is not allowed when the baseline or situation status is HIGH or CRITICAL, or "
                 "there is a first breach")
    change = ctx.facts.get("F.sit.headroom_change_vs_base", {}).get("value")
    if value == "opportunistic" and not (isinstance(change, (int, float)) and change > 0):
        p.append("opportunistic needs a favourable situation effect (F.sit.headroom_change_vs_base above zero)")
    probe = {"text": " ".join(headline) if isinstance(headline, list) else "", "cites": cites}
    if USD_AMOUNT.search(probe["text"]):
        p.append("write amounts in EUR m (each fact's eur_value), not USD m")
    return p + check_citations(probe, ctx) + check_numbers_cited(probe, ctx, "the headline") + check_names(probe, ctx)


# ------------------------------------------------------------------------------------------- the whole set
def validate_set(result: dict, pack: dict, tool_results: dict[str, dict] | None = None,
                 min_decisions: int = 3) -> dict:
    """Problems per item: {"set": [...], "position": [...], "assumptions": {id: [...]}, "to_verify": {id: [...]},
    "decisions": {index: [...]}, "alternatives": {index: [...]}}. Assumptions that fail are not available to the
    decisions that cite them (the decision then fails with "unknown assumption")."""
    assumptions = [a for a in result.get("assumptions", []) if isinstance(a, dict)]
    ctx0 = Context(pack, tool_results, assumptions, result.get("to_verify", []))
    bad_assumptions = {a.get("id", f"#{i}"): check_assumption(a, ctx0) for i, a in enumerate(assumptions)}
    bad_assumptions = {k: v for k, v in bad_assumptions.items() if v}
    ctx = Context(pack, tool_results, [a for a in assumptions if a.get("id") not in bad_assumptions],
                  result.get("to_verify", []))
    out = {"set": [], "position": check_position(result.get("position") or {}, ctx), "assumptions": bad_assumptions,
           "to_verify": {v.get("id", f"#{i}"): pr for i, v in enumerate(result.get("to_verify", []))
                         if (pr := check_to_verify(v, ctx))},
           "decisions": {}, "alternatives": {}}
    decisions = result.get("decisions", [])
    if not isinstance(decisions, list):
        out["set"].append("decisions must be a list")
        return out
    for i, d in enumerate(decisions):
        pr = check_decision(d, ctx) if isinstance(d, dict) else ["each decision must be an object"]
        if pr:
            out["decisions"][i] = pr
    scopes = [str(d.get("scope", "")).strip().casefold() for d in decisions if isinstance(d, dict)]
    if len(scopes) != len(set(scopes)):
        out["set"].append("two decisions have the same scope; merge them")
    if not min_decisions <= len(decisions) <= 5:
        out["set"].append(f"return {min_decisions} to 5 ranked decisions")
    for i, a in enumerate(result.get("rejected_alternatives", [])):
        pr = check_alternative(a, ctx) if isinstance(a, dict) else ["each alternative must be an object"]
        if pr:
            out["alternatives"][i] = pr
    return out


def passed(report: dict) -> bool:
    return not (report["set"] or report["position"] or report["assumptions"] or report["to_verify"]
                or report["decisions"] or report["alternatives"])

