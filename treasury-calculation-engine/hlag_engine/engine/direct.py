"""Direct cash flows (deviation from spec 2.1, 2.2, 2.7 and 4.3; see docs/decisions_pending.md, "Direct cash flows").

A scenario may state dated USD cash amounts directly (params.direct.cash_flows), for effects that do not pass
through a market driver, such as a fine, a settlement or a one-off payment named by a news Event. They are not a
driver module: the engine adds them as EVENT_CASH_FLOW lines (owner direct, label ASSUMPTION) to the scenario
pass only. The baseline pass has none, so each amount is its own scenario delta, reaches the scenario liquidity
path, headroom and status, and shows as the "direct" attribution bar. Deterministic: no LLM, network or randomness.
Entries dated after the horizon end are reported on a watch list, never as cash lines (5.2).
"""

from datetime import date
from decimal import Decimal

from hlag_engine.contracts.cash_line import CashLine, round_to_cents
from hlag_engine.contracts.enums import Currency, LineCode, OwnerSpec, PassType, ProvenanceLabel
from hlag_engine.contracts.scenario import ScenarioParams

M = Decimal(1_000_000)


def direct_cash_lines(params: ScenarioParams, as_of: date, horizon_end: date) -> tuple[list[CashLine], list[dict]]:
    """Scenario-pass cash lines for params.direct, and the entries dated after the horizon end."""
    lines: list[CashLine] = []
    watch: list[dict] = []
    for flow in params.direct.cash_flows:
        if flow.date <= as_of:  # the liquidity path starts the day after T0 (liquidity.path_dates)
            raise ValueError(f"direct cash flow {flow.effect_id} is dated {flow.date}; it must be after T0 {as_of}")
        amount = round_to_cents(Decimal(repr(flow.amount_usd_m)) * M)
        if flow.date > horizon_end:
            watch.append({"effect_id": flow.effect_id, "date": flow.date, "amount_usd": amount, "label": flow.label})
            continue
        lines.append(CashLine(sub_id=flow.effect_id, date=flow.date, currency=Currency.USD, amount=amount,
                              owner_spec=OwnerSpec.DIRECT, line_code=LineCode.EVENT_CASH_FLOW,
                              label=ProvenanceLabel.ASSUMPTION, pass_type=PassType.SCENARIO,
                              event_ids=list(flow.event_ids)))
    return lines, watch
