import json
from datetime import date
from pathlib import Path

import pytest

from treasury_gateway.forecast import ForecastAgent, ForecastRequest, ForecastOutput, compile_scenario, rule_forecast
from treasury_gateway.news_v8 import NewsV8Reader
from treasury_gateway.signals import IntelligenceStore


class Model:
    config = {"model": "mock-forecast-model"}
    def __init__(self, predictions):
        self.predictions = predictions
        self.calls = []
    def _chat(self, messages):
        self.calls.append(messages)
        return json.dumps({"predictions": self.predictions, "outlook": "Conditional estimate."})


class Replies(Model):
    def __init__(self, replies):
        super().__init__([])
        self.replies = iter(replies)

    def _chat(self, messages):
        self.calls.append(messages)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_model_explanatory_extras_and_fence_need_no_retry(tmp_path):
    raw = json.dumps({"predictions": [prediction(note="extra explanation")],
                      "outlook": "Conditional", "predictions_note": "Single assumption, not observed data."})
    model = Replies(["```json\n" + raw + "\n```"])
    agent, *_ = setup(tmp_path, model)
    result = agent.predict(ForecastRequest(as_of=date(2026, 11, 2)))
    assert result["scenario"]["bunker"]["vlsfo_shock_pct"] == .17
    assert len(model.calls) == 1


def test_invalid_model_prediction_corrected_once_before_saving(tmp_path):
    model = Replies([json.dumps({"predictions": [prediction(unit="bp")], "outlook": "Conditional"}),
                     json.dumps({"predictions": [prediction()], "outlook": "Conditional"})])
    agent, *_ = setup(tmp_path, model)
    result = agent.predict(ForecastRequest(as_of=date(2026, 11, 2)))
    assert result["predictions"][0]["unit"] == "pct"
    assert len(model.calls) == 2 and len(agent.forecasts()) == 1


def test_invalid_correction_is_not_saved_or_retried_forever(tmp_path):
    bad = json.dumps({"predictions": [prediction(event_ids=["Eforeign"])], "outlook": "Conditional"})
    model = Replies([bad, bad])
    agent, *_ = setup(tmp_path, model)
    with pytest.raises(ValueError, match="automatic correction"):
        agent.predict(ForecastRequest(as_of=date(2026, 11, 2)))
    assert len(model.calls) == 2 and agent.forecasts() == []


def test_provider_failure_is_not_retried(tmp_path):
    model = Replies([RuntimeError("provider unavailable")])
    agent, *_ = setup(tmp_path, model)
    with pytest.raises(RuntimeError):
        agent.predict(ForecastRequest(as_of=date(2026, 11, 2)))
    assert len(model.calls) == 1 and agent.forecasts() == []


def prediction(**overrides):
    return {"param_key": "bunker.vlsfo_shock_pct", "change": 17, "unit": "pct",
            "effective_date": "2026-11-02", "duration_days": 90, "confidence": .6,
            "rationale": "Estimated pass-through from route disruption; magnitude is an assumption.",
            "event_ids": ["E05a5cc9d32647c0990a5"], "label": "ASSUMPTION", **overrides}


def setup(tmp_path, model=None):
    source = Path(__file__).resolve().parents[3] / "news-engine-v8/examples/event-result.json"
    result = json.loads(source.read_text(encoding="utf-8"))
    result["events"][0]["title"] = "Red Sea attacks resume; vessels reroute"
    result["events"][0]["summary"] = "Ships reroute around the Cape of Good Hope after new attacks."
    reader = NewsV8Reader(tmp_path)
    reader.import_result(result)
    store = IntelligenceStore(":memory:")
    return ForecastAgent(store, reader, model), store, reader, result


def test_model_can_infer_unquoted_magnitude_but_labels_and_stores_assumption(tmp_path):
    model = Model([prediction()])
    agent, store, reader, result = setup(tmp_path, model)
    forecast = agent.predict(ForecastRequest(as_of=date(2026,11,2)))
    assert forecast["mode"] == "model"
    assert forecast["scenario"]["bunker"]["vlsfo_shock_pct"] == .17
    assert forecast["predictions"][0]["label"] == "ASSUMPTION"
    assert forecast["event_ids"] == [result["events"][0]["event_id"]]
    assert forecast["evidence_links"] and forecast["baseline_changed"] is False
    assert agent.get(forecast["forecast_id"]) == forecast
    assert len(model.calls) == 1
    assert not store.adjustments() and not store.signals()
    assert "FORWARD-LOOKING" in model.calls[0][0]["content"]


def test_rule_fallback_has_predictions_without_pretending_llm(tmp_path):
    agent, store, _, result = setup(tmp_path)
    output = agent.predict(ForecastRequest(as_of=date(2026,11,2), mode="rule_based"))
    assert output["mode"] == "rule_based" and output["model"] is None
    assert output["scenario"]["freight"]["transit_delay_days"]["ASIA_EUROPE"] == 12
    assert output["scenario"]["bunker"]["consumption_uplift_pct"] == .105
    assert all(p["label"] == "ASSUMPTION" for p in output["predictions"])
    assert "fixed rule assumptions" in output["assumption_notice"]
    assert not store.active_adjustments(date(2026,11,2))


@pytest.mark.parametrize("bad", [prediction(param_key="liquidity.cash_usd_m"),
                                    prediction(change=float("inf")), prediction(unit="bp"),
                                    prediction(change=201), prediction(label="MARKET"),
                                    prediction(change=True), prediction(confidence=True)])
def test_forecast_rejects_unbounded_or_observed_inputs(bad):
    with pytest.raises(ValueError):
        ForecastOutput.model_validate({"predictions": [bad], "outlook": "Conditional"})


def test_compiler_source_date_and_duplicate_guards():
    for bad in (prediction(event_ids=["Eforeign"]), prediction(effective_date="2026-12-01"),
                prediction(duration_days=100)):
        output = ForecastOutput.model_validate({"predictions": [bad], "outlook": "Conditional"})
        with pytest.raises(ValueError):
            compile_scenario(output, date(2026,11,2), 90, {"E05a5cc9d32647c0990a5"})
    output = ForecastOutput.model_validate({"predictions": [prediction(), prediction()], "outlook": "Conditional"})
    with pytest.raises(ValueError, match="Duplicate"):
        compile_scenario(output, date(2026,11,2), 90, {"E05a5cc9d32647c0990a5"})


def test_forecast_rejects_missing_future_or_foreign_source_context(tmp_path):
    agent, _, _, _ = setup(tmp_path, Model([prediction()]))
    with pytest.raises(ValueError, match="later than as_of"):
        agent.predict(ForecastRequest(as_of=date(2026,10,1)))
    with pytest.raises(ValueError, match="known v8"):
        agent.predict(ForecastRequest(as_of=date(2026,11,2), event_ids=["Eforeign"]))


def test_no_matching_rule_does_not_invent_exposure():
    out = rule_forecast([{"event_id":"Ex", "title":"An unrelated company reports earnings", "summary":"Quarterly results."}], date(2026,11,2), 90)
    assert out.predictions == []


@pytest.mark.parametrize("title", ["Fed rules out rate cuts", "Officials say no devaluation of CNY",
                                   "Euro rally fades", "Red Sea attacks have ceased"])
def test_keyword_fallback_abstains_on_explicitly_negated_narratives(title):
    out = rule_forecast([{"event_id": "Ex", "title": title, "summary": "Official statement."}], date(2026,11,2), 90)
    assert out.predictions == []


def test_single_event_forecast_name_and_selected_sources_survive_no_predictions(tmp_path):
    model=Model([])
    agent, _, reader, result=setup(tmp_path,model)
    event_id=result['events'][0]['event_id']
    output=agent.predict(ForecastRequest(as_of=date(2026,11,2),event_ids=[event_id]))
    assert output['scenario']['name']==result['events'][0]['title']
    assert output['selected_event_ids']==output['event_ids']==output['scenario']['event_ids']==[event_id]
    assert output['predictions']==[] and output['evidence_links']
    context=json.loads(model.calls[0][1]['content'])
    assert [event['event_id'] for event in context['events']]==[event_id]
    assert {source['article_id'] for source in context['original_articles']}==set(result['events'][0]['article_ids'])


def direct(**overrides):
    return prediction(**{"param_key": "direct.cash_flow", "change": -120, "unit": "usd_m", "effective_date": "2026-12-15",
                         "rationale": "The source reports a fine payable by the carrier; amount as reported.",
                         "effect_id": "fine-eu", "overlap_key": "eu-competition-fine", "cash_label": "EU fine", **overrides})


def test_model_direct_cash_flow_compiles_to_dated_engine_entry(tmp_path):
    agent, *_ = setup(tmp_path, Model([prediction(), direct()]))
    forecast = agent.predict(ForecastRequest(as_of=date(2026,11,2)))
    assert forecast["scenario"]["bunker"]["vlsfo_shock_pct"] == .17
    [flow] = forecast["scenario"]["direct"]["cash_flows"]
    assert flow == {"effect_id": "fine-eu", "overlap_key": "eu-competition-fine", "date": "2026-12-15",
                    "amount_usd_m": -120, "label": "EU fine", "rationale": direct()["rationale"],
                    "event_ids": ["E05a5cc9d32647c0990a5"]}


@pytest.mark.parametrize("bad, message", [
    (dict(effect_id=None), "needs effect_id"),
    (dict(change=-2500), "outside input limits"),
    (dict(unit="pct"), "outside input limits"),
])
def test_direct_cash_prediction_contract(bad, message):
    with pytest.raises(ValueError, match=message):
        ForecastOutput.model_validate({"predictions": [direct(**bad)], "outlook": "x"})
    with pytest.raises(ValueError, match="direct.cash_flow only"):
        ForecastOutput.model_validate({"predictions": [prediction(effect_id="x")], "outlook": "x"})


def test_direct_cash_compile_rejects_t0_date_double_counting_and_total_cap():
    allowed = {"E05a5cc9d32647c0990a5"}
    def compile_(*items):
        return compile_scenario(ForecastOutput.model_validate({"predictions": list(items), "outlook": "x"}),
                                date(2026,11,2), 90, allowed)
    with pytest.raises(ValueError, match="after as_of"):
        compile_(direct(effective_date="2026-11-02"))
    with pytest.raises(ValueError, match="share one overlap_key"):
        compile_(direct(), direct(effect_id="fine-2", effective_date="2026-12-20"))
    with pytest.raises(ValueError, match="Duplicate direct cash flow"):
        compile_(direct(), direct())
    with pytest.raises(ValueError, match="total absolute"):
        compile_(*[direct(effect_id=f"f{i}", overlap_key=f"k{i}", change=-1800, effective_date=f"2026-12-1{i}") for i in range(3)])
    # one effect paid in two instalments
    assert len(compile_(direct(), direct(effective_date="2027-01-15"))["direct"]["cash_flows"]) == 2


def test_model_context_carries_t0_fx_rates_for_direct_cash_conversion(tmp_path):
    model = Model([direct()])
    source = Path(__file__).resolve().parents[3] / "news-engine-v8/examples/event-result.json"
    reader = NewsV8Reader(tmp_path)
    reader.import_result(json.loads(source.read_text(encoding="utf-8")))
    agent = ForecastAgent(IntelligenceStore(":memory:"), reader, model, fx_rates=lambda day: {"EUR": 1.1165, "GBP": None})
    agent.predict(ForecastRequest(as_of=date(2026,11,2)))
    context = json.loads(model.calls[0][1]["content"])
    assert context["t0_fx_usd_per_unit"] == {"EUR": 1.1165}
    assert "t0_fx_usd_per_unit" in model.calls[0][0]["content"]
    def failing(day):
        raise OSError("no market store")
    agent = ForecastAgent(IntelligenceStore(":memory:"), reader, Model([direct()]), fx_rates=failing)
    assert agent.predict(ForecastRequest(as_of=date(2026,11,2)))["scenario"]["direct"]


def test_server_sets_the_assumption_label_but_validates_everything_else(tmp_path):
    """A model that miscopies the constant label still yields a forecast; other fields stay strictly validated."""
    agent, *_ = setup(tmp_path, Model([prediction(label="FOMC_Rate_Cut")]))
    forecast = agent.predict(ForecastRequest(as_of=date(2026,11,2)))
    assert forecast["predictions"][0]["label"] == "ASSUMPTION"
    agent, *_ = setup(tmp_path / "missing", Model([{k: v for k, v in prediction().items() if k != "label"}]))
    assert agent.predict(ForecastRequest(as_of=date(2026,11,2)))["predictions"][0]["label"] == "ASSUMPTION"
    agent, *_ = setup(tmp_path / "bad", Model([prediction(change=500)]))
    with pytest.raises(ValueError, match="automatic correction"):
        agent.predict(ForecastRequest(as_of=date(2026,11,2)))
    assert len(agent.client.calls) == 2 and agent.forecasts() == []


def test_forecast_endpoint_reuses_saved_result_without_second_model_call(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from treasury_gateway.forecast import create_forecast_router
    model = Model([prediction()])
    _, store, reader, _ = setup(tmp_path, model)
    app = FastAPI()
    app.include_router(create_forecast_router(store, reader, model))
    with TestClient(app) as client:
        body = {'as_of': '2026-11-02', 'horizon_days': 90}
        a = client.post('/forecast', json=body)
        b = client.post('/forecast', json=body)
    assert a.status_code == b.status_code == 200
    assert a.json()['forecast_id'] == b.json()['forecast_id']
    assert len(model.calls) == 1
