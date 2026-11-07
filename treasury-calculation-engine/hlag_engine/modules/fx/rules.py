"""FX Tier A rules (9B.8) and playbook (9B.11). Thresholds are PROPOSED ILLUSTRATIVE SETTINGS."""

from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, AlertThresholds, OnSevere, ThresholdLevel
from hlag_engine.contracts.enums import OwnerSpec, PlaybookTriggerType
from hlag_engine.contracts.playbook import PlaybookEntry

REVIEW_OWNER = "treasury_desk"
MAJOR, EMERGING = ("EUR", "CAD", "GBP"), ("INR", "BRL", "CNY")
VOL_REGIME = {"elevated": 1.25, "stressed": 2.0}  # short / long volatility ratio (4.5, ASSUMPTION)


def alert_rules() -> list[AlertRule]:
    rules = []
    for ccys, (w, s) in ((MAJOR, (1.0, 2.0)), (EMERGING, (0.75, 1.5))):
        for ccy in ccys:
            rules.append(AlertRule(
                rule_id=f"fx.rate_move.{ccy.lower()}", module=OwnerSpec.FX, series=[f"FX_USD_PER_{ccy}"],
                type=AlertRuleType.PCT_MOVE, window="1d", unit="pct", lookback=60,
                thresholds=AlertThresholds(warning=ThresholdLevel(value=w, vol_multiple=2.5),
                                           severe=ThresholdLevel(value=s, vol_multiple=4)),
                on_severe=OnSevere.REQUEST_AUTO_BUILD, description=f"Daily move of USD per {ccy}"))
    for ccy in (*MAJOR, *EMERGING):
        rules.append(AlertRule(
            rule_id=f"fx.vol_regime.{ccy.lower()}", module=OwnerSpec.FX, series=[f"FX_USD_PER_{ccy}"],
            type=AlertRuleType.VOL_REGIME, window="20d", unit="ratio", lookback=260,
            thresholds=AlertThresholds(warning=ThresholdLevel(value=VOL_REGIME["elevated"]),
                                       severe=ThresholdLevel(value=VOL_REGIME["stressed"])),
            description="20-day volatility over its 1-year average: warning when it moves into elevated "
                        "(1.25x), severe when it moves into stressed (2.0x)"))
    return rules


def playbook() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(entry_id="fx.eur_rally", trigger_type=PlaybookTriggerType.PRESET_TYPE, trigger="eur_rally",
                      actions=["Raise the EUR hedge ratio for operating costs and the dividend (test with test_hedge)",
                               "Buy EUR earlier"], hedge_tool="eur_operating_costs", review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="fx.em_stress", trigger_type=PlaybookTriggerType.PRESET_TYPE, trigger="em_stress",
                      actions=["Review the hedge on INR and BRL obligations",
                               "Minimise cash held in those currencies"],
                      hedge_tool="inr_brl_obligations", review_owner=REVIEW_OWNER),
        PlaybookEntry(entry_id="fx.volatility_spike", trigger_type=PlaybookTriggerType.ALERT_RULE,
                      trigger="fx.vol_regime",
                      actions=["Shorten the review cycle", "Check that the CAD programme is within its 80% cap"],
                      hedge_tool="cad_operating_costs", review_owner=REVIEW_OWNER),
    ]
