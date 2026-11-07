"""Timeline snapping (spec 2.5, D17): days 1-91 keep their date; later lines move to the last day of their
month. Lines that land on the same stable key are summed (weakest label), before pairing."""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from hlag_engine.contracts.cash_line import CashLine
from hlag_engine.contracts.enums import PassType, ProvenanceLabel
from hlag_engine.generator.calendar import month_end

DAILY_DAYS = 91


def snap_date(d: date, as_of: date) -> date:
    return d if d <= as_of + timedelta(days=DAILY_DAYS) else month_end(d)


def snap(lines: Sequence[CashLine], as_of: date, pass_type: PassType | None = None) -> list[CashLine]:
    """Snap and merge; pass_type, when given, is set on every resulting line (the orchestrator's field, 2.1)."""
    merged: dict[tuple, dict] = {}
    for line in lines:
        when = snap_date(line.date, as_of)
        key = (line.owner_spec, line.line_code, when, line.currency, line.sub_id)
        if key in merged:
            m = merged[key]
            m["amount"] += line.amount
            m["label"] = ProvenanceLabel.weakest(m["label"], line.label)
        else:
            merged[key] = {**line.model_dump(), "date": when, "amount": Decimal(line.amount)}
            if pass_type is not None:
                merged[key]["pass_type"] = pass_type
    return [CashLine.model_validate(v) for k, v in sorted(merged.items(), key=lambda kv: _order(kv[0]))]


def _order(key):
    owner, code, when, currency, sub_id = key
    return (when, owner.value, code.value, currency.value, sub_id)
