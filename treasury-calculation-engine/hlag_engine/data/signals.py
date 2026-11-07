"""Signal storage (spec 10.6, D63), P3, P34.

submit_signal validates each signal with contracts.signal.submit_signal (field checks, 2.8),
then stores the accepted ones. A signal whose event_id is already stored with an equal or
higher version is rejected as stale (named error stale_version). A higher version is stored
and the previous current version is marked superseded (superseded_by_version, superseded_at).
Signals in one call are processed in order, so two versions of one event in a batch work.

Re-routing, re-running auto-built runs and withdrawing proposals for a new version (D63) are
done by the router and engine in later steps.
"""

from typing import Any

from hlag_engine.contracts.signal import RejectedSignal, Signal, SubmitSignalResponse
from hlag_engine.contracts.signal import submit_signal as validate_signals
from hlag_engine.data.store import Store


def submit_signal(
    store: Store, signals: Signal | dict[str, Any] | list[Signal | dict[str, Any]], actor: str = "news_engine"
) -> SubmitSignalResponse:
    items = signals if isinstance(signals, list) else [signals]
    response = SubmitSignalResponse()
    for item in items:
        checked = validate_signals(item)
        if checked.rejected:
            response.rejected.extend(checked.rejected)
            continue
        raw = item.model_dump(mode="json") if isinstance(item, Signal) else item
        signal = Signal.model_validate(raw)
        with store.transaction():
            latest = store.latest_signal_version(signal.event_id)
            if latest is not None and signal.version <= latest:
                response.rejected.append(
                    RejectedSignal(
                        event_id=signal.event_id,
                        errors=[
                            f"stale_version: version {signal.version} of {signal.event_id} is not higher"
                            f" than the stored version {latest}"
                        ],
                    )
                )
                store.append_audit(actor, "signal_rejected_stale", signal.event_id,
                                   {"version": signal.version, "stored_version": latest})
                continue
            store.insert_signal(signal)
            store.append_audit(actor, "signal_stored", signal.event_id, {"version": signal.version})
            if latest is not None:
                store.mark_signal_superseded(signal.event_id, latest, signal.version)
                store.append_audit(actor, "signal_superseded", signal.event_id,
                                   {"version": latest, "superseded_by_version": signal.version})
        response.accepted.append(signal.event_id)
    return response
