"""Signal storage and stale-version rejection (10.6, D63, P3, P34)."""

from hlag_engine.contracts.signal import Signal
from hlag_engine.data.signals import submit_signal
from tests.contracts.test_signal import RED_SEA, signal


def test_accepted_signal_is_stored(store):
    response = submit_signal(store, signal())
    assert response.accepted == [RED_SEA["event_id"]] and response.rejected == []
    stored = store.get_signal(RED_SEA["event_id"])
    assert stored.signal == Signal.model_validate(RED_SEA) and stored.superseded_by_version is None
    assert store.list_audit(action="signal_stored")


def test_invalid_signal_rejected_and_not_stored(store):
    response = submit_signal(store, signal(event_type="alien_invasion"))
    assert response.accepted == [] and response.rejected[0].errors[0].startswith("invalid_event_type")
    assert store.get_signal(RED_SEA["event_id"]) is None


def test_equal_or_lower_version_rejected_as_stale(store):
    submit_signal(store, signal(version=2))
    for version in (2, 1):
        response = submit_signal(store, signal(version=version, headline="changed"))
        assert response.accepted == []
        assert response.rejected[0].errors[0].startswith("stale_version")
        assert response.rejected[0].event_id == RED_SEA["event_id"]
    assert store.get_signal(RED_SEA["event_id"]).signal.headline == RED_SEA["headline"]
    assert len(store.list_audit(action="signal_rejected_stale")) == 2


def test_higher_version_supersedes(store):
    submit_signal(store, signal())
    response = submit_signal(store, signal(version=2, confidence=0.9))
    assert response.accepted == [RED_SEA["event_id"]]
    versions = store.list_signal_versions(RED_SEA["event_id"])
    assert [(v.signal.version, v.superseded_by_version) for v in versions] == [(1, 2), (2, None)]
    assert versions[0].superseded_at is not None
    assert store.get_signal(RED_SEA["event_id"]).signal.confidence == 0.9
    assert [s.version for s in store.list_current_signals()] == [2]
    assert store.list_audit(action="signal_superseded")[0].details == {"version": 1, "superseded_by_version": 2}


def test_batch_processed_in_order(store):
    response = submit_signal(store, [signal(), signal(version=3), signal(version=2), signal(event_id="evt_other")])
    assert response.accepted == [RED_SEA["event_id"], RED_SEA["event_id"], "evt_other"]
    assert [r.errors[0].split(":")[0] for r in response.rejected] == ["stale_version"]
    assert [v.superseded_by_version for v in store.list_signal_versions(RED_SEA["event_id"])] == [3, None]


def test_signal_object_accepted(store):
    assert submit_signal(store, Signal.model_validate(RED_SEA)).accepted == [RED_SEA["event_id"]]
