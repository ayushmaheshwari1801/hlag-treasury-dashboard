"""Explanations. Numbers are never computed here: text may only restate figures already in the result.

A pluggable provider (e.g. an LLM) may write the prose; ``guard`` rejects any text that contains a
number absent from the result, or that states a different status, and falls back to the template.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Protocol

from .models import D, ModuleResult, Status, quantize

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TOKEN = re.compile(r"(?<![A-Za-z_\-.\d])[-+]?\d[\d,]*(?:\.\d+)?")


class ExplanationProvider(Protocol):
    def explain(self, result_json: dict) -> str: ...


def _numbers(obj, out: set[Decimal]):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(D(obj))
        out.add(D(obj) * 100)                       # ratios may be quoted as percentages
    elif isinstance(obj, str):
        for t in TOKEN.findall(ISO_DATE.sub(" ", obj)):
            try:
                out.add(D(t.replace(",", "")))
            except Exception:
                pass
        try:
            out.add(D(obj))
        except Exception:
            pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)


def allowed_numbers(result: ModuleResult) -> set[Decimal]:
    out: set[Decimal] = set()
    _numbers(result.model_dump(mode="json"), out)
    out.update({D(30), D(60), D(90)})
    return out


def guard(text: str, result: ModuleResult) -> tuple[bool, list[str]]:
    problems = []
    allowed = allowed_numbers(result)
    for token in TOKEN.findall(ISO_DATE.sub(" ", text)):
        value = D(token.replace(",", ""))
        places = len(token.split(".")[1]) if "." in token else 0
        if not any(quantize(a, places) == value or quantize(a, places) == -value and token.startswith("+") for a in allowed):
            problems.append(f"number {token} is not in the computed result")
    scrubbed = re.sub(r"[Rr]eliability\s+(LOW|MEDIUM|HIGH)", "", text)
    for status in Status:
        if status != result.assessment.status and re.search(rf"\b{re.escape(status.value)}\b", scrubbed):
            problems.append(f"text names status {status.value} but the rules engine says {result.assessment.status.value}")
    return not problems, problems


def deterministic_explanation(result: ModuleResult) -> str:
    h, a, m = result.headline, result.assessment, result.market
    parts = [f"{result.title}: "]
    if m.latest_value is not None:
        parts.append(f"latest {m.latest_value:g} {m.unit} on {m.latest_date} ({m.source_kind}, {m.freshness.lower()}). ")
    if h and h.impact.value is not None:
        parts.append(f"At the selected scenario ({h.label}) the {result.impact_type.value} is {h.impact.value:g} {h.impact.unit} "
                     f"[{h.impact.provenance.value}]. ")
    else:
        parts.append("No impact can be computed because a required input is missing. ")
    parts.append(f"Status {a.status.value} under {a.rule_id} ({a.threshold_version}); reliability {result.reliability.value}. ")
    if a.liquidity_ratio is not None:
        parts.append(f"Adverse impact is {a.liquidity_ratio:.2%} of comparable liquidity. ")
    if a.missing:
        parts.append("Missing: " + "; ".join(sorted(set(a.missing))) + ". ")
    parts.append("Thresholds are demonstration values, not company policy. This explains the calculation; it does not change it.")
    return "".join(parts)


def explain(result: ModuleResult, provider: ExplanationProvider | None = None) -> dict:
    fallback = deterministic_explanation(result)
    if provider is None:
        return {"text": fallback, "source": "deterministic", "guard": {"passed": True, "problems": []}}
    try:
        text = provider.explain(result.model_dump(mode="json"))
    except Exception as exc:                        # provider failure never blocks the numbers
        return {"text": fallback, "source": "deterministic_fallback", "guard": {"passed": False, "problems": [f"provider error: {exc}"]}}
    ok, problems = guard(text, result)
    return {"text": text if ok else fallback, "source": "provider" if ok else "deterministic_fallback",
            "guard": {"passed": ok, "problems": problems}}
