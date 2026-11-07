"""Run summary (spec 4.8): returned by run_scenario for every run and stored in the record's results."""

from decimal import Decimal

from hlag_engine.contracts.alert import AlertExplanation
from hlag_engine.contracts.run_record import RunRecord
from hlag_engine.contracts.run_summary import Attribution as AttributionM
from hlag_engine.contracts.run_summary import DeskException, Headroom, RunSummary, ScenarioRef
from hlag_engine.contracts.enums import ShortfallTier
from hlag_engine.engine.liquidity import shortfall_tier

M = Decimal(1_000_000)


def usd_m(x: Decimal) -> float:
    return round(float(x / M), 3)


def build_summary(record: RunRecord, status, liq, attribution, exceptions: list[DeskException],
                  alerts: list[AlertExplanation]) -> RunSummary:
    p = record.params
    return RunSummary(
        run_id=record.run_id, as_of=record.as_of,
        scenario=ScenarioRef(name=p.name, source=p.source, preset_id=p.preset_id, event_ids=list(p.event_ids)),
        status=status.status, opportunity=status.opportunity, flags=record.flags,
        headroom=Headroom(
            baseline_min_usd_m=usd_m(liq.h_b), baseline_min_date=liq.h_b_date,
            scenario_min_usd_m=usd_m(liq.h_s), scenario_min_date=liq.h_s_date,
            consumed_pct=None if liq.c_pct is None else round(float(liq.c_pct), 2),
            first_breach_date=liq.breach,
            shortfall_tier=shortfall_tier(liq.scen, liq.h_s_date, liq.inputs) if liq.h_s_date else ShortfallTier.RESERVE_BREACHED,
        ),
        attribution_usd_m=AttributionM(freight=usd_m(attribution.freight), bunker=usd_m(attribution.bunker),
                                       rates=usd_m(attribution.rates), fx=usd_m(attribution.fx),
                                       direct=usd_m(attribution.direct),
                                       total=usd_m(attribution.total)),
        desk_exceptions=exceptions, alert_ids=[a.alert_id for a in alerts],
    )
