"""Bunker Tier A rules (7.9) and playbook (7.12). Thresholds are PROPOSED ILLUSTRATIVE SETTINGS."""

from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, AlertThresholds, OnSevere, ThresholdLevel
from hlag_engine.contracts.enums import OwnerSpec, PlaybookTriggerType
from hlag_engine.contracts.playbook import PlaybookEntry

REVIEW_OWNER = "treasury_desk"
FUEL_SERIES = ("VLSFO_SIN", "VLSFO_RTM", "HSFO_SIN", "HSFO_RTM")


def _t(warning: ThresholdLevel, severe: ThresholdLevel) -> AlertThresholds:
    return AlertThresholds(warning=warning, severe=severe)


def alert_rules() -> list[AlertRule]:
    rules = [
        AlertRule(rule_id=f"bunker.hub_price_move.{sid.lower()}", module=OwnerSpec.BUNKER, series=[sid],
                  type=AlertRuleType.PCT_MOVE, window="1w", unit="pct", lookback=26,
                  thresholds=_t(ThresholdLevel(value=7.5, vol_multiple=2.5), ThresholdLevel(value=15, vol_multiple=4)),
                  on_severe=OnSevere.REQUEST_AUTO_BUILD, description=f"Weekly move of {sid} (shared default)")
        for sid in FUEL_SERIES
    ]
    rules.append(AlertRule(rule_id="bunker.early_warning_brent", module=OwnerSpec.BUNKER, series=["BRENT"],
                           type=AlertRuleType.PCT_MOVE, window="1d", unit="pct",
                           thresholds=_t(ThresholdLevel(value=4), ThresholdLevel(value=7)),
                           description="Daily Brent move as an early warning"))
    for hub in ("SIN", "RTM"):
        rules.append(AlertRule(rule_id=f"bunker.spread_move.{hub.lower()}", module=OwnerSpec.BUNKER,
                               series=[f"VLSFO_{hub}", f"HSFO_{hub}"], type=AlertRuleType.SPREAD_CHANGE,
                               window="1w", unit="usd_per_t",
                               thresholds=_t(ThresholdLevel(value=30), ThresholdLevel(value=60)),
                               description=f"Weekly change of the VLSFO - HSFO spread at {hub}"))
    rules.append(AlertRule(rule_id="bunker.brent_vlsfo_divergence", module=OwnerSpec.BUNKER,
                           series=["VLSFO_BLENDED", "BRENT"], type=AlertRuleType.DIVERGENCE, window="4w", unit="pp",
                           thresholds=_t(ThresholdLevel(value=10), ThresholdLevel(value=20)),
                           description="4-week % change of hub-weighted VLSFO minus that of Brent; reference: "
                                       "Brent +19.8% vs MFO +45.2% in H1 2026 (ratio 2.28), -18.5% vs -26.2% in 2025"))
    return rules


def playbook() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(entry_id="bunker.sustained_rise", trigger_type=PlaybookTriggerType.ALERT_RULE,
                      trigger="bunker.hub_price_move",
                      actions=["Test a tactical hedge with the hedge tool", "Flag MFR recovery for commercial review",
                               "Raise the liquidity buffer"],
                      hedge_tool="fuel_all", review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="bunker.sharp_fall", trigger_type=PlaybookTriggerType.ALERT_RULE,
                      trigger="bunker.hub_price_move",
                      actions=["Plan for faster cash release", "Check margin exposure if a swap has been added"],
                      hedge_tool="fuel_all", review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="bunker.red_sea", trigger_type=PlaybookTriggerType.PRESET_TYPE,
                      trigger="red_sea_reescalation",
                      actions=["Run the joint preset with freight to see the net effect of higher rates, slower "
                               "collections and higher burn"],
                      review_owner=REVIEW_OWNER),
    ]
