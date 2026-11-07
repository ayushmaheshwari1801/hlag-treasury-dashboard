from copy import deepcopy
from treasury_gateway.event_actions import event_actions


def finance():
    return {"horizon_months": 6, "all_actions": [],
            "cash_impact": {"net_eur_m": -12.5},
            "liquidity": {"scenario_headroom_eur_m": 800, "scenario_min_date": "2027-02-28"},
            "calculation": {"run_id": "cash1", "status": "HIGH"}}


def event(id="E1", score=60, advice="Review bunker procurement timing."):
    return {"event_id": id, "title": "Shipping disruption", "what_to_do": [advice],
            "article_ids": ["A" + id], "score": {"total": score}}


def test_selected_event_retains_original_advice_and_exact_calculation():
    f = finance()
    actions = event_actions(f, [event(), event("E2", 99)], ["E1"], True)
    assert len(actions) == 1
    a = actions[0]
    assert a["text"] == event()["what_to_do"][0]
    assert a["event_ids"] == ["E1"] and a["article_ids"] == ["AE1"]
    assert a["module"] == "bunker" and a["severity"] == "HIGH"
    assert "-12.50m" in a["context"] and "+800.00m" in a["context"]
    assert "2027-02-28" in a["context"] and "edited scenario" in a["context"]
    assert a["run_id"] == "cash1"


def test_unselected_events_are_ranked_deduplicated_and_never_given_unrelated_cash():
    events = [event("E1", 50), event("E2", 80), event("E3", 70, "Review funding options.")]
    original = deepcopy(events)
    actions = event_actions(finance(), events)
    assert len(actions) == 2
    assert actions[0]["event_ids"] == ["E2", "E1"]
    assert actions[0]["article_ids"] == ["AE2", "AE1"]
    assert all("Not quantified" in a["context"] and "12.50" not in a["context"] for a in actions)
    assert all(a["run_id"] is None for a in actions)
    assert events == original


def test_missing_data_warning_is_preserved_and_unknown_impact_is_not_zero():
    f = finance()
    warning = {"rule_id": "cash_data_missing"}
    f["all_actions"] = [warning, {"rule_id": "review:bunker"}]
    f["cash_impact"]["net_eur_m"] = None
    actions = event_actions(f, [event()], ["E1"])
    assert actions[0] == warning
    assert len(actions) == 2 and "not a zero-impact" in actions[1]["context"]
    assert "unavailable" in actions[1]["context"]
