"""Rates Tier A rules (8.9) and playbook (8.12). Thresholds are PROPOSED ILLUSTRATIVE SETTINGS."""

from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, AlertThresholds, OnSevere, ThresholdLevel
from hlag_engine.contracts.enums import OwnerSpec, PlaybookTriggerType
from hlag_engine.contracts.playbook import PlaybookEntry

REVIEW_OWNER = "treasury_desk"


def _rule(rule_id, series, window, warning, severe, on_severe, description):
    return AlertRule(rule_id=rule_id, module=OwnerSpec.RATES, series=[series], type=AlertRuleType.BP_MOVE,
                     window=window, unit="bp",
                     thresholds=AlertThresholds(warning=ThresholdLevel(value=warning), severe=ThresholdLevel(value=severe)),
                     on_severe=on_severe, description=description)


def alert_rules() -> list[AlertRule]:
    auto = OnSevere.REQUEST_AUTO_BUILD
    return [
        _rule("rates.expectation_shift.ust_2y", "UST_2Y", "1w", 15, 30, auto, "Weekly move of the US 2-year yield"),
        _rule("rates.expectation_shift.ea_2y", "EA_2Y", "1w", 15, 30, auto, "Weekly move of the euro area 2-year yield"),
        _rule("rates.credit_spread.bb_oas", "BB_OAS", "1w", 25, 50, auto, "Weekly move of the BB spread"),
        _rule("rates.benchmark_step.sofr", "SOFR", "1d", 10, 20, OnSevere.NONE,
              "Daily SOFR change (the news engine carries the decision itself)"),
        _rule("rates.benchmark_step.estr", "ESTR", "1d", 10, 20, OnSevere.NONE,
              "Daily ESTR change (the news engine carries the decision itself)"),
    ]


def playbook() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(entry_id="rates.rates_rising", trigger_type=PlaybookTriggerType.ALERT_RULE,
                      trigger="rates.expectation_shift",
                      actions=["Test pay-fixed swaps with the swap tool", "Pre-fund or bring forward refinancing"],
                      hedge_tool="floating_debt_usd", review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="rates.rates_falling", trigger_type=PlaybookTriggerType.PRESET_TYPE,
                      trigger="fed_cutting_cycle",
                      actions=["Lengthen deposit terms to lock in yields (test receive-fixed)",
                               "Consider repaying floating debt with surplus cash"],
                      hedge_tool="deposits_usd", review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="rates.spreads_widening", trigger_type=PlaybookTriggerType.ALERT_RULE,
                      trigger="rates.credit_spread",
                      actions=["Pre-fund maturities", "Check credit line availability", "Run the credit squeeze preset"],
                      review_owner=REVIEW_OWNER),
    ]
