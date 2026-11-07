"""Freight Tier A rules (6.9) and playbook (6.12). Thresholds are PROPOSED ILLUSTRATIVE SETTINGS."""

from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, AlertThresholds, OnSevere, ThresholdLevel
from hlag_engine.contracts.enums import OwnerSpec, PlaybookTriggerType
from hlag_engine.contracts.playbook import PlaybookEntry
from hlag_engine.modules.freight.calc import INDEX_SERIES

REVIEW_OWNER = "treasury_desk"


def alert_rules() -> list[AlertRule]:
    rules = []
    for trade, series in INDEX_SERIES.items():
        rules.append(AlertRule(
            rule_id=f"freight.route_move.{series.lower()}", module=OwnerSpec.FREIGHT, series=[series],
            type=AlertRuleType.PCT_MOVE, window="1w", unit="pct", lookback=26,
            thresholds=AlertThresholds(warning=ThresholdLevel(value=10, vol_multiple=2.5),
                                       severe=ThresholdLevel(value=20, vol_multiple=4)),
            on_severe=OnSevere.REQUEST_AUTO_BUILD, description=f"Weekly move of {series} (shared default)",
        ))
        rules.append(AlertRule(
            rule_id=f"freight.index_realised_gap.{trade.value.lower()}", module=OwnerSpec.FREIGHT,
            series=[series, f"FREIGHT_REALISED_{trade.value}"], type=AlertRuleType.DIVERGENCE, window="1w",
            unit="pp", thresholds=AlertThresholds(warning=ThresholdLevel(value=15), severe=ThresholdLevel(value=25)),
            description="Weekly change in the gap between the model's estimated realised rate and the index "
                        "(the realised rate is a model estimate)",
        ))
    return rules


def playbook() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(entry_id="freight.sustained_fall", trigger_type=PlaybookTriggerType.STATUS, trigger="HIGH",
                      actions=["Raise the liquidity buffer", "Secure or extend facilities",
                               "Review discretionary payment timing", "Press collections"],
                      review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="freight.spike", trigger_type=PlaybookTriggerType.ALERT_RULE,
                      trigger="freight.route_move",
                      actions=["Plan deployment of the surplus", "Schedule conversion with the FX workflow",
                               "Prepare for reversal by running the reversal preset"],
                      review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="freight.disruption", trigger_type=PlaybookTriggerType.PRESET_TYPE,
                      trigger="transit_delay",
                      actions=["Model slower transit and collections",
                               "Check minimum-headroom dates against payment calendars"],
                      review_owner=REVIEW_OWNER),
    ]
