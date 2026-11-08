"""Validator for recommendation-agent/3: pure unit tests, no engine. Includes the deliberate fabrication cases (an
invented number, an invented event, an invented bank name, an assumption without a basis), the owner roles, the
bunker engine-exception rule, overlap/conflict news inputs and the 'trend continues' assumption."""
import copy

import pytest

from treasury_gateway.fact_pack import fact
from treasury_gateway.recommendation_validator import passed, validate_set

RATE = 1.1165


def pack(status="WATCH", base_status="LOW", bunker_exceptional=False, first_breach="none", bunker_driver="price"):
    F = [
        fact("F.fx.t0_usd_per_eur", "USD per EUR at T0", RATE, "USD per EUR", "MARKET", "Engine", "2026-11-08"),
        fact("F.liq.credit_lines_unused", "Unused credit lines", 725.0, "USD m", "DISCLOSED", "Profile", rate=RATE),
        fact("F.liq.base.min_headroom", "Baseline lowest headroom", 2899.6, "USD m", "ENGINE", "Engine", "2026-12-31", RATE),
        fact("F.liq.base.min_date", "Baseline lowest date", "2026-12-31", None, "ENGINE", "Engine"),
        fact("F.liq.base.status", "Baseline status", base_status, None, "ENGINE", "Engine"),
        fact("F.liq.base.first_breach", "Baseline first breach", "none", None, "ENGINE", "Engine"),
        fact("F.sit.status", "Situation status", status, None, "ENGINE", "Engine"),
        fact("F.sit.first_breach", "Situation first breach", first_breach, None, "ENGINE", "Engine"),
        fact("F.sit.min_headroom", "Situation lowest headroom", 2500.0, "USD m", "ENGINE", "Engine", "2027-05-31", RATE),
        fact("F.sit.headroom_change_vs_base", "Situation minus baseline", -399.6, "USD m", "ENGINE", "Engine", rate=RATE),
        fact("F.pay.1", "Scheduled payment: dividend", -588.7, "USD m", "ENGINE", "Engine", "2027-05-31", RATE),
        fact("F.mkt.VLSFO_RTM.chg_4w", "VLSFO_RTM 4-week change", 12.22, "%", "DERIVED", "ClearLynx", "2026-10-30"),
        fact("F.mkt.VLSFO_RTM.chg_1w", "VLSFO_RTM 1-week change", 1.93, "%", "DERIVED", "ClearLynx", "2026-10-30"),
        fact("F.mkt.ESTR.chg_1w", "ESTR 1-week change", -0.3, "bp", "DERIVED", "ECB", "2026-11-05"),
        fact("F.mkt.ESTR.chg_4w", "ESTR 4-week change", 1.1, "bp", "DERIVED", "ECB", "2026-11-05"),
        fact("F.news.E1", "Bunker prices surge after refinery outage", 60, "score", "NEWS", "Example Wire", "2026-10-30"),
        fact("F.news.E2", "Fuel glut expected", 40, "score", "NEWS", "Example Wire", "2026-10-28"),
        fact("F.news.E1.input.bunker.vlsfo_shock_pct", "News input", 20, "pct", "NEWS", "Saved model forecast",
             "2026-11-08", origin="model_saved", used=True, confidence=0.6, event_id="E1", param_key="bunker.vlsfo_shock_pct"),
        fact("F.news.E2.input.bunker.vlsfo_shock_pct", "News input", -10, "pct", "NEWS", "Saved model forecast",
             "2026-11-08", origin="model_saved", used=False, confidence=0.6, event_id="E2", param_key="bunker.vlsfo_shock_pct"),
        fact("F.news.conflict.bunker.vlsfo_shock_pct", "News Events disagree", ["E1 up", "E2 down"], None, "DERIVED",
             "News forecast inputs", used_event_id="E1"),
        fact("F.policy.cad", "FX hedge ratio cad", 70, "%", "ASSUMPTION", "Profile", cap_pct=80, room_pct_points=10),
        fact("F.policy.eur_dividend", "FX hedge ratio eur dividend", 0, "%", "ASSUMPTION", "Profile", cap_pct=100, closed=True),
        fact("F.policy.bunker", "Bunker hedging policy", "derivatives only in exceptional cases", None, "DISCLOSED", "AR"),
        fact("F.eng.bunker_exceptional", "Engine marks fuel as exceptional", bunker_exceptional, None, "ENGINE", "Engine"),
        fact("F.sit.bunker_driver", "What moves fuel cash", bunker_driver, None, "DERIVED", "News forecast inputs"),
        fact("F.eng.trend_continues.min_headroom", "Lowest headroom if last week's moves repeat", 2890.0, "USD m",
             "ASSUMPTION", "Observed moves run", rate=RATE),
        fact("F.bank.BANK_A", "Relationship bank BANK_A (generic register name)", "BANK_A", None, "ASSUMPTION", "Register"),
        fact("F.der.materiality_threshold", "Materiality threshold: 1% of the baseline lowest headroom", 29.0, "USD m",
             "DERIVED", "Calculated from Baseline lowest headroom", rate=RATE),
        fact("F.sit.row.interest", "News-case cash effect: interest", 0.0, "USD m", "ENGINE", "Engine", rate=RATE),
        fact("F.alert.fx.vol_regime.brl", "Market alert (warning): BRL volatility", 1.6, "ratio", "ENGINE", "Engine alerts", "2026-11-06"),
        fact("F.policy.cad.cap", "Hedge ratio cap cad", 80, "%", "DISCLOSED", "Annual report"),
        fact("F.trade.ASIA_EUROPE", "Trade lane Asia-Europe", "Asia–Europe", None, "DISCLOSED", "Investor Report H1 2026, p. 10"),
        fact("F.mkt.BRENT.level", "BRENT latest real print", 64.2, "USD/bbl", "MARKET", "ICE", "2026-10-30"),
    ]
    facts = {f["id"]: f for f in F}
    return {"facts": facts, "ticked": ["E1", "E2"], "names": ["BANK_A", "Treasury", "CFO"],
            "article_text": "Bunker prices surge after refinery outage in Singapore. Fuel glut expected.",
            "conflicts": [{"input": "bunker.vlsfo_shock_pct", "used_event_id": "E1",
                           "events": [{"event_id": "E1", "direction": "up", "change": 20, "unit": "pct"},
                                      {"event_id": "E2", "direction": "down", "change": -10, "unit": "pct"}]}]}


def decision(verb, scope, owner, trigger, **kw):
    d = {"verb": verb, "scope": scope, "owner": {"role": owner, "kind": "business function"},
         "amount_or_limit": {"text": "no limit set", "cites": []},
         "timing": {"text": "before 31 Dec 2026", "cites": ["F.liq.base.min_date"]},
         "trigger": trigger, "why": [], "expected_effect": {"text": "not quantified", "candidate_id": None, "cites": []},
         "cash_at_stake": {"text": "The news lowers the lowest headroom by EUR 357.9m.", "label": "Engine",
                           "cites": ["F.sit.headroom_change_vs_base"]},
         "effort": "low", "reversibility": "easy", "if_wrong": {"text": "Little is lost.", "cites": []},
         "escalate_if": [{"text": "If the 4-week fuel move exceeds 12.22% again.", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]}],
         "usual_practice": "Treasurers keep committed lines ready ahead of a seasonal low point.",
         "event_ids": [], "assumption_ids": [], "to_verify_ids": []}
    d.update(kw)
    return d


def good_set():
    return {
        "position": {"value": "heightened_monitoring",
                     "headline": ["The ticked news lowers the lowest headroom by EUR 357.9m.",
                                  "The liquidity risk status is watch."],
                     "cites": ["F.sit.headroom_change_vs_base", "F.sit.status"]},
        "decisions": [
            decision("Prepare", "drawdown readiness on the unused credit lines", "Treasury",
                     {"type": "Forecast", "date": "2026-10-30", "source": "Example Wire", "cites": ["F.news.E1"]},
                     amount_or_limit={"text": "up to EUR 649m", "cites": ["F.liq.credit_lines_unused"]},
                     why=[{"text": "News points to fuel 20% higher.", "label": "News forecast",
                           "cites": ["F.news.E1.input.bunker.vlsfo_shock_pct"]},
                          {"text": "Two Events conflict on the fuel move, so the size is uncertain.", "label": "Fact",
                           "cites": ["F.news.conflict.bunker.vlsfo_shock_pct"]},
                          {"text": "Fuel already rose over four weeks.", "label": "AI assumption", "cites": ["A1"]}],
                     event_ids=["E1"], assumption_ids=["A1"]),
            decision("Monitor", "fuel price alerts", "Procurement",
                     {"type": "Occurred", "date": "2026-10-30", "source": "ClearLynx", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]},
                     why=[{"text": "Fuel rose 12.22% in four weeks.", "label": "Fact", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]}],
                     escalate_if=[{"text": "If last week's moves repeat.", "cites": ["A2"]}]),
            decision("Confirm", "the payment calendar for the dividend", "CFO",
                     {"type": "Structural", "date": "2027-05-31", "source": "Engine", "cites": ["F.pay.1"]},
                     why=[{"text": "The dividend of EUR 527.3m falls on 31 May 2027.", "label": "Engine", "cites": ["F.pay.1"]}],
                     to_verify_ids=["V1"]),
        ],
        "rejected_alternatives": [{"title": "Hedge fuel with a swap",
                                   "reason": {"text": "Fuel derivatives are used only in exceptional cases.",
                                              "cites": ["F.policy.bunker", "F.eng.bunker_exceptional"]},
                                   "candidate_id": None}],
        "assumptions": [{"id": "A1", "statement": "Fuel keeps rising at the recent pace.", "basis": ["F.mkt.VLSFO_RTM.chg_4w"],
                         "likelihood": "likely", "effect_if_wrong": "Fuel costs are lower than planned."},
                        {"id": "A2", "statement": "Last week's moves repeat.", "basis": ["F.eng.trend_continues.min_headroom"],
                         "likelihood": "possible", "effect_if_wrong": "No added pressure."}],
        "to_verify": [{"id": "V1", "check": "Actual dividend payment date and amount.", "why": "Internal fact."}],
        "events_used": [{"event_id": "E1", "status": "used_in_decision", "decision": 1, "reason": "It drives the fuel forecast."},
                        {"event_id": "E2", "status": "considered_not_acted", "reason": "Its forecast overlaps a stronger Event."}],
    }


def problems(result, p=None, tools=None):
    return validate_set(result, p or pack(), tools)


def flat(report):
    out = list(report["set"]) + list(report["position"])
    for k in ("assumptions", "to_verify", "decisions", "alternatives"):
        for v in report[k].values():
            out += v
    return out


def test_a_grounded_set_passes():
    r = problems(good_set())
    assert passed(r), flat(r)


# ---------------------------------------------------------------------------------- fabrication cases
def test_invented_number_is_rejected():
    s = good_set()
    s["decisions"][0]["amount_or_limit"]["text"] = "up to EUR 640m"
    r = problems(s)
    assert any("number 640" in x and "F.liq.credit_lines_unused = 725.0 USD m / 649.4 EUR m" in x for x in r["decisions"][0])


def test_number_from_another_items_facts_is_rejected():
    s = good_set()
    s["decisions"][1]["why"].append({"text": "The dividend is EUR 527.3m.", "label": "Fact", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]})
    r = problems(s)  # 527.3 is in the pack (F.pay.1, cited by decision 3) but this item does not cite it
    assert any("number 527.3" in x for x in r["decisions"][1]) and 2 not in r["decisions"]


def test_invented_event_is_rejected():
    s = good_set()
    s["decisions"][0]["event_ids"] = ["E999"]
    assert any("event E999 is not a ticked Event" in x for x in problems(s)["decisions"][0])
    s = good_set()
    s["decisions"][0]["trigger"]["cites"] = ["F.news.E999"]
    out = problems(s)["decisions"][0]
    assert any("unknown citation F.news.E999" in x for x in out) and any("E999" in x and "ticked" in x for x in out)


def test_invented_bank_name_is_rejected_and_register_name_is_accepted():
    s = good_set()
    s["decisions"][0]["scope"] = "drawdown readiness with the Deutsche Bank facility"
    assert any("name 'Deutsche Bank'" in x for x in problems(s)["decisions"][0])
    s = good_set()
    s["decisions"][0]["scope"] = "drawdown readiness with BANK_A"
    assert passed(problems(s))


def test_assumption_without_a_basis_is_rejected():
    s = good_set()
    s["assumptions"][0]["basis"] = []
    r = problems(s)
    assert any("needs a basis" in x for x in r["assumptions"]["A1"])
    assert any("unknown citation A1" in x or "unknown assumption A1" in x for x in r["decisions"][0])
    s = good_set()
    s["assumptions"][0]["basis"] = ["F.made.up"]
    assert any("F.made.up is not a fact id" in x for x in problems(s)["assumptions"]["A1"])


# ---------------------------------------------------------------------------------- assumptions and trend
def test_possible_assumption_cannot_support_a_decision_but_may_escalate():
    s = good_set()
    s["decisions"][1]["assumption_ids"] = ["A2"]
    assert any("A2 is possible" in x for x in problems(s)["decisions"][1])
    assert passed(problems(good_set()))  # A2 inside escalate_if only: accepted


def test_trend_continues_only_as_possible_assumption_in_escalate_if():
    s = good_set()
    s["decisions"][1]["why"].append({"text": "Pressure may build.", "label": "Engine", "cites": ["F.eng.trend_continues.min_headroom"]})
    assert any("last week's moves repeat" in x for x in problems(s)["decisions"][1])
    s = good_set()
    s["assumptions"][1]["likelihood"] = "likely"
    assert any("must be possible" in x for x in problems(s)["assumptions"]["A2"])
    s = good_set()
    s["decisions"][1]["escalate_if"] = [{"text": "If last week's moves repeat.", "cites": ["F.eng.trend_continues.min_headroom"]}]
    assert passed(problems(s))
    s = good_set()
    s["position"]["cites"].append("F.eng.trend_continues.min_headroom")
    assert any("trend_continues" in x for x in problems(s)["position"])


# ---------------------------------------------------------------------------------- phrasing and owners
def test_execution_wording_is_rejected():
    s = good_set()
    s["decisions"][0]["verb"] = "Buy"
    s["decisions"][0]["scope"] = "a 50% swap now"
    out = problems(s)["decisions"][0]
    assert any("verb must be one of" in x for x in out) and any("never execution" in x or "execute" in x for x in out)


@pytest.mark.parametrize("owner", ["Head of Treasury", "Anna Schmidt", "Treasury Front Office"])
def test_owner_must_be_one_of_six_business_functions(owner):
    s = good_set()
    s["decisions"][0]["owner"]["role"] = owner
    assert any("not one of the six business functions" in x for x in problems(s)["decisions"][0])


@pytest.mark.parametrize("owner", ["Treasury", "CFO", "Credit Control", "Procurement", "Commercial/Pricing", "Risk Committee"])
def test_all_six_business_functions_are_accepted(owner):
    s = good_set()
    s["decisions"][0]["owner"]["role"] = owner
    assert passed(problems(s)), flat(problems(s))


def test_person_name_in_text_is_rejected():
    s = good_set()
    s["decisions"][2]["why"].append({"text": "Ask Anna Schmidt to confirm.", "label": "To verify", "cites": ["V1"]})
    assert any("name 'Anna Schmidt'" in x for x in problems(s)["decisions"][2])


# ---------------------------------------------------------------------------------- hedges and policy
def hedge(cid, scope, cites, owner="Treasury"):
    return decision("Propose", scope, owner, {"type": "Forecast", "date": "2026-10-30", "source": "x", "cites": ["F.news.E1"]},
                    kind="hedge", expected_effect={"text": "Engine-tested effect.", "candidate_id": cid, "cites": [cid]},
                    why=[{"text": "Tested by the engine.", "label": "Engine", "cites": [cid, *cites]}], event_ids=["E1"])


def test_closed_fx_key_cannot_be_a_decision():
    tools = {"cand_div": {"candidate_id": "cand_div", "grade": "material", "module": "fx",
                          "spec": {"key": "fx.hedge_ratio.eur_dividend", "from": 0.0, "to": 0.5}}}
    s = good_set()
    s["decisions"][2] = hedge("cand_div", "a review of the dividend hedge ratio", ["F.policy.eur_dividend"])
    assert any("closed by policy" in x for x in problems(s, tools=tools)["decisions"][2])


def test_bunker_hedge_needs_the_engine_exception_not_the_position():
    tools = {"cand_fuel": {"candidate_id": "cand_fuel", "grade": "material", "module": "bunker",
                           "spec": {"instrument": "swap", "target": "fuel_all", "coverage_pct": 25, "tenor_months": 12}}}
    s = good_set()
    s["position"]["value"] = "defensive"
    s["decisions"][2] = hedge("cand_fuel", "a 25% fuel swap for 12 months", ["F.policy.bunker", "F.eng.bunker_exceptional"])
    out = problems(s, pack(bunker_exceptional=False), tools)["decisions"][2]
    assert any("F.eng.bunker_exceptional is false" in x for x in out)
    assert 2 not in problems(s, pack(bunker_exceptional=True), tools)["decisions"]


def test_hedge_without_a_tested_candidate_is_rejected():
    s = good_set()
    s["decisions"][2] = decision("Propose", "a CAD hedge ratio of 80%", "Treasury",
                                 {"type": "Structural", "date": "2026-11-08", "source": "x", "cites": ["F.policy.cad"]})
    assert any("tested candidate" in x for x in problems(s)["decisions"][2])


# ---------------------------------------------------------------------------------- news overlap and conflict
def test_overlapping_input_not_added_cannot_support_a_decision():
    s = good_set()
    s["decisions"][0]["why"].append({"text": "Another Event sees fuel 10% lower.", "label": "News forecast",
                                     "cites": ["F.news.E2.input.bunker.vlsfo_shock_pct"]})
    assert any("not added" in x for x in problems(s)["decisions"][0])


def test_conflicted_input_needs_the_conflict_stated_as_uncertainty():
    s = good_set()
    s["decisions"][0]["why"] = [w for w in s["decisions"][0]["why"] if "conflict" not in w["cites"][0]]
    assert any("conflict" in x and "E1 and E2" in x for x in problems(s)["decisions"][0])
    assert passed(problems(good_set()))  # with the conflict fact and an uncertainty statement: accepted


# ---------------------------------------------------------------------------------- set level
def test_business_as_usual_not_allowed_with_high_status_or_a_breach():
    s = good_set()
    s["position"]["value"] = "business_as_usual"
    assert any("business_as_usual is not allowed" in x for x in problems(s, pack(status="HIGH"))["position"])
    assert any("business_as_usual is not allowed" in x for x in problems(s, pack(first_breach="2027-02-28"))["position"])


def test_headline_is_two_sentences_and_set_has_one_to_five_unique_decisions():
    s = good_set()
    s["position"]["headline"] = ["Only one sentence."]
    assert any("exactly two sentences" in x for x in problems(s)["position"])
    s = good_set()
    s["decisions"] = []
    s["events_used"][0] = {"event_id": "E1", "status": "considered_not_acted", "reason": "No action needed."}
    assert any("1 to 5" in x for x in problems(s)["set"])
    s = good_set()
    s["decisions"] = s["decisions"] * 2
    assert any("1 to 5" in x for x in problems(s)["set"])
    s = good_set()
    s["decisions"][1]["scope"] = s["decisions"][0]["scope"]
    assert any("same scope" in x for x in problems(s)["set"])


def test_trigger_type_must_cite_matching_facts():
    s = good_set()
    s["decisions"][1]["trigger"]["cites"] = ["F.news.E1"]  # Occurred needs market facts
    assert any("Occurred trigger must cite" in x for x in problems(s)["decisions"][1])



# ---------------------------------------------------------------------------------- name check (common words)
def name_probe(text, p=None):
    s = good_set()
    s["decisions"][1]["why"].append({"text": text, "label": "Fact", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]})
    return problems(s, p)["decisions"].get(1, [])


@pytest.mark.parametrize("text, name", [("Maersk raised its rates.", "Maersk"),
                                        ("Fuel risk grows with a contract with Shell.", "Shell")])
def test_company_names_are_rejected_unless_in_the_facts_or_cited_articles(text, name):
    assert any(f"name '{name}'" in x for x in name_probe(text))
    p = pack()
    p["article_text"] += f" {name} is named in the cited article."
    assert name_probe(text, p) == []


def test_ordinary_sentence_start_word_is_accepted():
    assert name_probe("Review the reserve.") == []


def test_single_capitalised_name_mid_sentence_is_always_checked():
    assert any("name 'Hamburg'" in x for x in name_probe("The terminal in Hamburg is slower."))
    assert any("name 'Deutsche Bank'" in x for x in name_probe("Deutsche Bank offers a line."))



# ---------------------------------------------------------------------------------- round 5: verbs, timing, stake, trend
@pytest.mark.parametrize("verb", ["Hold", "Increase", "Reduce", "Defer", "Accelerate", "Raise", "Escalate"])
def test_new_verbs_are_accepted(verb):
    s = good_set()
    s["decisions"][1]["verb"] = verb
    assert passed(problems(s)), flat(problems(s))


@pytest.mark.parametrize("text", ["within 2 weeks", "within 30 days", "in the next 6 weeks", "before the low point on 31 Dec 2026"])
def test_relative_timing_or_a_fact_date_is_accepted(text):
    s = good_set()
    s["decisions"][1]["timing"] = {"text": text, "cites": ["F.liq.base.min_date"]}
    assert passed(problems(s)), flat(problems(s))


def test_other_dates_and_figures_in_timing_are_rejected():
    s = good_set()
    s["decisions"][1]["timing"] = {"text": "before 15 Feb 2027", "cites": ["F.liq.base.min_date"]}
    assert any("date 15 Feb 2027" in x for x in problems(s)["decisions"][1])
    s = good_set()
    s["decisions"][1]["timing"] = {"text": "once fuel passes 900 USD/t", "cites": ["F.liq.base.min_date"]}
    assert any("number 900" in x for x in problems(s)["decisions"][1])


def test_cash_at_stake_is_required_and_must_cite_a_cash_or_headroom_fact():
    s = good_set()
    del s["decisions"][1]["cash_at_stake"]
    assert any("cash_at_stake is required" in x for x in problems(s)["decisions"][1])
    s = good_set()
    s["decisions"][1]["cash_at_stake"] = {"text": "Fuel rose 12.22% in four weeks.", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]}
    assert any("cash_at_stake must cite a cash or headroom fact" in x for x in problems(s)["decisions"][1])
    s = good_set()
    s["decisions"][1]["cash_at_stake"] = {"text": "EUR 2588.4m if last week repeats.", "cites": ["F.eng.trend_continues.min_headroom"]}
    assert 1 in problems(s)["decisions"]


def test_a_trend_is_likely_only_when_one_week_and_four_week_moves_agree():
    s = good_set()  # A1 rests on VLSFO: 1w +1.93%, 4w +12.22% (same direction) -> likely is fine
    assert passed(problems(s))
    s["assumptions"][0]["basis"] = ["F.mkt.ESTR.chg_1w"]  # ESTR: 1w -0.3 bp, 4w +1.1 bp -> mixed
    s["assumptions"][0]["statement"] = "The euro rate keeps moving."
    assert any("do not have the same direction" in x for x in problems(s)["assumptions"]["A1"])
    s["assumptions"][0]["likelihood"] = "possible"
    assert "A1" not in problems(s)["assumptions"]



def test_amounts_must_be_in_eur_not_usd_millions():
    s = good_set()
    s["decisions"][0]["why"].append({"text": "Unused lines are 725.0 USD m.", "label": "Fact", "cites": ["F.liq.credit_lines_unused"]})
    assert any("EUR m" in x for x in problems(s)["decisions"][0])
    s = good_set()
    s["position"]["headline"][0] = "Headroom falls by USD 399.6m."
    assert any("EUR m" in x for x in problems(s)["position"])
    s = good_set()
    s["decisions"][1]["why"].append({"text": "Fuel is quoted in USD/t and the rate in USD per EUR.", "label": "Fact",
                                     "cites": ["F.mkt.VLSFO_RTM.chg_4w"]})
    assert passed(problems(s)), flat(problems(s))


def test_scope_starting_with_an_ordinary_word_is_not_a_name():
    s = good_set()
    s["decisions"][0]["scope"] = "Access to the unused credit lines"
    assert passed(problems(s)), flat(problems(s))



def test_bunker_price_hedge_is_rejected_when_consumption_drives_fuel_cash():
    tools = {"cand_fuel": {"candidate_id": "cand_fuel", "grade": "material", "module": "bunker",
                           "spec": {"instrument": "swap", "target": "fuel_all", "coverage_pct": 25, "tenor_months": 12}}}
    s = good_set()
    s["decisions"][2] = hedge("cand_fuel", "a 25% fuel swap for 12 months", ["F.policy.bunker", "F.eng.bunker_exceptional"])
    out = problems(s, pack(bunker_exceptional=True, bunker_driver="consumption"), tools)["decisions"][2]
    assert any("consumption, not price" in x and "operations review" in x for x in out)
    assert 2 not in problems(s, pack(bunker_exceptional=True, bunker_driver="price"), tools)["decisions"]



@pytest.mark.parametrize("text", ["Collections on Asia-Europe accounts are slower.", "The VLSFO-versus-Brent spread widened.",
                                  "Watch the Asia–Europe lane."])
def test_hyphenated_words_are_accepted_when_every_part_is_ordinary_or_in_the_facts(text):
    assert name_probe(text) == []


def test_hyphenated_word_with_an_unknown_part_is_rejected():
    assert any("Acme-Maersk" in x for x in name_probe("The Acme-Maersk alliance cut capacity."))



# ---------------------------------------------------------------------------------- round 8
def test_iso_dates_internal_terms_and_status_codes_are_rejected_in_text():
    for text, reason in (("Collections slow before 2026-12-31.", "write dates like 31 Dec 2026"),
                         ("The situation worsens.", "do not use the word 'situation'"),
                         ("The fuel module drives it.", "do not use the word 'module'"),
                         ("Status stays LOW.", "lower case ('low')")):
        s = good_set()
        s["decisions"][1]["why"].append({"text": text, "label": "Fact", "cites": ["F.liq.base.min_date", "F.mkt.VLSFO_RTM.chg_4w"]})
        assert any(reason in x for x in problems(s)["decisions"][1]), (text, problems(s)["decisions"].get(1))


def test_one_decision_is_enough():
    s = good_set()
    s["decisions"] = s["decisions"][:1]
    s["events_used"][0]["decision"] = 1
    assert passed(problems(s)), flat(problems(s))


def test_immaterial_cash_at_stake_needs_an_alert_or_policy_trigger_and_monitor_or_confirm():
    s = good_set()
    s["decisions"][1]["cash_at_stake"] = {"text": "EUR 0.0m for interest.", "label": "Engine", "cites": ["F.sit.row.interest"]}
    assert any("immaterial" in x for x in problems(s)["decisions"][1])           # market-move trigger, verb Monitor
    s["decisions"][1]["trigger"] = {"type": "Occurred", "cites": ["F.alert.fx.vol_regime.brl"], "source": "alert"}
    assert 1 not in problems(s)["decisions"]                                       # alert trigger + Monitor: allowed
    s["decisions"][1]["verb"] = "Prepare"
    assert any("immaterial" in x for x in problems(s)["decisions"][1])           # alert trigger but wrong verb


def test_model_assumption_label_for_profile_assumptions_and_fact_for_disclosed_caps():
    s = good_set()
    s["decisions"][1]["why"].append({"text": "The register lists BANK_A.", "label": "Fact", "cites": ["F.bank.BANK_A"]})
    assert any("labelled Model assumption" in x for x in problems(s)["decisions"][1])
    s["decisions"][1]["why"][-1]["label"] = "Model assumption"
    assert 1 not in problems(s)["decisions"]
    s["decisions"][1]["why"].append({"text": "The CAD cap is 80%.", "label": "Fact", "cites": ["F.policy.cad.cap"]})
    assert 1 not in problems(s)["decisions"]
    s["decisions"][1]["why"].append({"text": "Fuel keeps rising.", "label": "AI assumption", "cites": ["F.mkt.VLSFO_RTM.chg_4w"]})
    assert any("assumption id from this session" in x for x in problems(s)["decisions"][1])


def test_every_ticked_event_must_be_listed_with_a_status():
    s = good_set()
    s["events_used"] = s["events_used"][:1]
    assert any("missing from events_used: E2" in x for x in problems(s)["set"])
    s = good_set()
    s["events_used"][1] = {"event_id": "E2", "status": "not_quantifiable", "reason": "No input."}
    assert any("to_verify_id" in x for x in problems(s)["events_used"][1])
    s["events_used"][1]["to_verify_id"] = "V1"
    assert passed(problems(s)), flat(problems(s))
    s["events_used"][0]["decision"] = 9
    assert any("decision number" in x for x in problems(s)["events_used"][0])



# ---------------------------------------------------------------------------------- round 9
@pytest.mark.parametrize("text, ok", [("Headroom is EUR 2,597.0m.", True), ("Headroom is EUR -357.9m.", True),
                                      ("Headroom is 2597.0 EUR m.", False), ("Headroom is EUR 2597.0m.", False),
                                      ("Headroom is EUR 2,597.0 m.", False),
                                      ("The low point is 31 Dec 2026, EUR 2,597.0m.", True)])
def test_amounts_are_written_like_the_page(text, ok):
    s = good_set()
    s["decisions"][1]["why"].append({"text": text, "label": "Engine", "cites": ["F.liq.base.min_headroom", "F.sit.headroom_change_vs_base"]})
    out = problems(s)["decisions"].get(1, [])
    assert (not any("write amounts like the page" in x for x in out)) is ok, out


def test_no_cash_effect_needs_a_supporting_fact():
    s = good_set()
    s["events_used"][1] = {"event_id": "E2", "status": "no_cash_effect", "reason": "No forecast input."}
    assert any("no_cash_effect needs a cited fact" in x for x in problems(s)["events_used"][1])
    s["events_used"][1]["cites"] = ["F.news.E2"]                                    # the Event itself is not evidence
    assert 1 in problems(s)["events_used"]
    s["events_used"][1].update(cites=["F.news.E2.input.bunker.vlsfo_shock_pct"],
                               reason="Its fuel input was not added, so it has no effect in the forecast.")
    assert passed(problems(s)), flat(problems(s))


def test_statement_citing_only_engine_facts_is_labelled_engine():
    s = good_set()
    s["decisions"][2]["why"][0]["label"] = "Fact"                                   # cites only F.pay.1 (engine)
    assert any("label it Engine, not Fact" in x for x in problems(s)["decisions"][2])
    s["decisions"][2]["why"][0]["label"] = "Engine"
    assert 2 not in problems(s)["decisions"]


# ---------------------------------------------------------------------------------- round 10
def severe_pack():
    p = pack()
    p["facts"]["F.alert.divergence.vlsfo_brent"] = fact(
        "F.alert.divergence.vlsfo_brent", "Severe: VLSFO rose faster than Brent over 4 weeks (VLSFO +12.2%, Brent +3.1%)",
        9.1, "pct", "ENGINE", "Engine alerts", "2026-10-30", severity="severe")
    return p


def test_a_severe_alert_must_be_in_a_trigger_or_a_rejected_alternative():
    p = severe_pack()
    r = problems(good_set(), p)
    assert any("severe alert" in x and "is not addressed" in x for x in r["set"]), r["set"]
    s = good_set()
    s["decisions"][1]["trigger"]["cites"].append("F.alert.divergence.vlsfo_brent")
    assert not any("severe alert" in x for x in problems(s, p)["set"])
    s = good_set()
    s["rejected_alternatives"][0]["reason"]["cites"].append("F.alert.divergence.vlsfo_brent")
    assert not any("severe alert" in x for x in problems(s, p)["set"])


def test_a_warning_alert_need_not_be_addressed():
    assert not any("severe alert" in x for x in problems(good_set())["set"])


def test_amount_that_repeats_the_scope_is_rejected():
    s = good_set()
    s["decisions"][0]["amount_or_limit"]["text"] = "drawdown readiness on the unused credit lines"
    assert any("amount_or_limit repeats the scope" in x for x in problems(s)["decisions"][0])
    assert not any("repeats the scope" in x for x in problems(good_set())["decisions"].get(0, []))


def test_numbers_in_a_cited_event_title_or_article_text_count_as_cited():
    p = pack()
    p["facts"]["F.news.E2"]["article_text"] = "A state fund takes a 25% stake in a fuel supplier; supply stays ample."
    s = good_set()
    s["events_used"][1]["reason"] = "A 25% stake changes ownership, not fuel cash, in the horizon."
    assert passed(problems(s, p)), flat(problems(s, p))
    s["events_used"][1]["reason"] = "A 35% stake changes ownership, not fuel cash, in the horizon."
    assert any("number 35" in x for x in problems(s, p)["events_used"][1])
