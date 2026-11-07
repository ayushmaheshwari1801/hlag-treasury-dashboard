import copy

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import Signal, SubmitSignalResponse, submit_signal

# The qualitative Red Sea example from spec 2.8.
RED_SEA = {
    "event_id": "evt_20261102_redsea_001",
    "version": 1,
    "published_at": "2026-11-02T06:30:00+00:00",
    "headline": "Attacks on shipping resume in the southern Red Sea",
    "summary": "Carriers report renewed attacks near Bab el-Mandeb; several services are "
    "re-routing via the Cape of Good Hope.",
    "evidence_links": ["https://example.com/news/red-sea-attacks"],
    "event_type": "trade_disruption",
    "kind": "qualitative",
    "affected_drivers": ["freight", "bunker"],
    "affected_trades": ["ASIA_EUROPE"],
    "affected_currencies": [],
    "affected_countries": ["YE", "EG"],
    "named_entities": [],
    "direction": "up",
    "magnitude": None,
    "magnitude_unit": None,
    "magnitude_subject": None,
    "suggested_preset": "red_sea_reescalation",
    "confidence": 0.8,
    "expected_duration_days": 90,
}

SOFR_CUT = {
    **RED_SEA,
    "event_id": "evt_20261210_fed_001",
    "event_type": "central_bank_decision",
    "kind": "quantitative",
    "affected_drivers": ["rates"],
    "affected_trades": [],
    "affected_currencies": ["USD"],
    "affected_countries": ["US"],
    "direction": "down",
    "magnitude": -25,
    "magnitude_unit": "bp",
    "magnitude_subject": "SOFR",
    "suggested_preset": None,
}


def signal(**overrides):
    data = copy.deepcopy(RED_SEA)
    data.update(overrides)
    return data


def errors_for(**overrides):
    response = submit_signal(signal(**overrides))
    assert response.accepted == []
    assert len(response.rejected) == 1
    return response.rejected[0].errors


def test_spec_example_is_accepted():
    response = submit_signal(RED_SEA)
    assert response == SubmitSignalResponse(accepted=["evt_20261102_redsea_001"], rejected=[])
    assert response.model_dump() == {"accepted": ["evt_20261102_redsea_001"], "rejected": []}


def test_quantitative_signal_accepted():
    assert submit_signal(SOFR_CUT).accepted == ["evt_20261210_fed_001"]


def test_signal_object_accepted():
    assert submit_signal(Signal.model_validate(RED_SEA)).accepted == [RED_SEA["event_id"]]


@pytest.mark.parametrize(
    ("overrides", "error_name"),
    [
        ({"event_type": "war"}, "invalid_event_type"),
        ({"magnitude": 10, "kind": "quantitative", "magnitude_unit": "usd"}, "invalid_magnitude_unit"),
        ({"affected_trades": ["TRANSPACIFIC"]}, "invalid_affected_trades"),
        ({"affected_currencies": ["XYZ"]}, "unknown_currency_code"),
        ({"affected_currencies": ["usd"]}, "unknown_currency_code"),
        ({"affected_countries": ["UK"]}, "unknown_country_code"),
        ({"affected_countries": ["YEM"]}, "unknown_country_code"),
        ({"evidence_links": []}, "missing_evidence_link"),
        ({"evidence_links": ["not a url"]}, "invalid_evidence_links"),
        ({"kind": "rumour"}, "invalid_kind"),
        ({"affected_drivers": ["equity"]}, "invalid_affected_drivers"),
        ({"direction": "sideways"}, "invalid_direction"),
        ({"suggested_preset": "red_sea"}, "invalid_suggested_preset"),
        ({"confidence": 1.2}, "invalid_confidence"),
        ({"confidence": -0.1}, "invalid_confidence"),
        ({"version": 0}, "invalid_version"),
        ({"version": "1"}, "invalid_version"),
        ({"published_at": "2026-11-02T06:30:00"}, "invalid_published_at"),
        ({"expected_duration_days": 0}, "invalid_expected_duration_days"),
        ({"headline": ""}, "invalid_headline"),
        ({"magnitude": 10}, "magnitude_not_allowed"),
        ({"kind": "quantitative"}, "missing_magnitude"),
        ({"kind": "quantitative", "magnitude": 10}, "missing_magnitude_unit"),
        ({"sentiment": "bad"}, "unknown_field_sentiment"),
    ],
)
def test_rejected_with_named_error(overrides, error_name):
    errors = errors_for(**overrides)
    assert any(e.startswith(f"{error_name}:") for e in errors), errors


def test_missing_required_field_named():
    data = signal()
    del data["evidence_links"]
    del data["event_type"]
    response = submit_signal(data)
    names = {e.split(":")[0] for e in response.rejected[0].errors}
    assert {"missing_evidence_links", "missing_event_type"} <= names
    assert response.rejected[0].event_id == RED_SEA["event_id"]


def test_rejected_without_event_id_reports_none():
    data = signal()
    del data["event_id"]
    rejected = submit_signal(data).rejected[0]
    assert rejected.event_id is None
    assert any(e.startswith("missing_event_id:") for e in rejected.errors)


def test_all_errors_reported_together():
    errors = errors_for(event_type="war", confidence=2, affected_countries=["ZZ"])
    names = {e.split(":")[0] for e in errors}
    assert names == {"invalid_event_type", "invalid_confidence", "unknown_country_code"}


def test_batch_split_into_accepted_and_rejected():
    bad = signal(event_id="evt_bad", event_type="war")
    response = submit_signal([RED_SEA, bad, SOFR_CUT, "not a signal"])
    assert response.accepted == [RED_SEA["event_id"], SOFR_CUT["event_id"]]
    assert [r.event_id for r in response.rejected] == ["evt_bad", None]
    assert response.rejected[1].errors[0].startswith("invalid_signal:")


def test_empty_list_returns_empty_response():
    assert submit_signal([]) == SubmitSignalResponse()


def test_signal_model_rejects_directly():
    with pytest.raises(ValidationError):
        Signal.model_validate(signal(event_type="war"))


def test_signal_json_round_trip():
    s = Signal.model_validate(SOFR_CUT)
    assert Signal.model_validate_json(s.model_dump_json()) == s
