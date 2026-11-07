import sqlite3

import pytest
from fastapi.testclient import TestClient

from treasury_sensitivity.api import create_app
from treasury_sensitivity.audit import AuditStore

BASE = "/api/v1/sensitivity"


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


def test_health_reports_versions(client):
    body = client.get("/healthz").json()
    assert body["formula_version"] == "formulas-1.0" and body["threshold_version"] == "demo-thresholds-1.0"


def test_overview_has_four_cards_with_status_and_provenance(client):
    cards = client.get(f"{BASE}/overview", params={"as_of": "2026-10-31"}).json()["cards"]
    assert [c["module"] for c in cards] == ["fx", "interest", "bunker", "freight"]
    for c in cards:
        assert c["status"] in ("CRITICAL", "HIGH", "LOW", "NOT ASSESSABLE") and c["reliability"] == "LOW"
        assert c["market_provenance"] == "ILLUSTRATIVE" and "NOT company policy" in c["thresholds_note"]


def test_scenario_endpoint_recalculates_and_explains(client):
    r = client.post(f"{BASE}/modules/bunker/scenario", json={"overrides": {"consumption_mt": 1.2}}).json()
    assert r["result"]["headline"]["impact"]["value"] == -48.0
    assert r["explanation"]["source"] == "deterministic" and "demonstration values" in r["explanation"]["text"]


def test_reset_restores_defaults_and_logs(client):
    changed = client.post(f"{BASE}/modules/bunker/scenario", json={"overrides": {"consumption_mt": 1.2}}).json()
    reset = client.post(f"{BASE}/modules/bunker/reset", params={"actor": "jenna"}).json()
    assert reset["result"]["headline"]["impact"]["value"] == -96.0 != changed["result"]["headline"]["impact"]["value"]
    assert client.get(f"{BASE}/audit").json()["entries"][0]["event_type"] == "reset"


def test_defaults_endpoint_exposes_bounds(client):
    controls = {c["key"]: c for c in client.get(f"{BASE}/modules/freight/defaults").json()["controls"]}
    assert controls["transmission_k"]["maximum"] == 1 and controls["scfi_change_pct"]["quantity"]["value"] == 8.0


def test_history_endpoint(client):
    h = client.get(f"{BASE}/modules/fx/history", params={"range": "3M"}).json()
    assert h["interpolated"] is False and h["point_count"] > 0 and h["market"]["unit"] == "USD per CAD"
    assert client.get(f"{BASE}/modules/fx/history", params={"range": "9Y"}).status_code == 422
    assert client.get(f"{BASE}/modules/interest/history", params={"benchmark": "NOPE"}).status_code == 422


def test_errors_are_clean(client):
    assert client.post(f"{BASE}/modules/crypto/scenario", json={}).status_code == 404
    assert client.post(f"{BASE}/modules/fx/scenario", json={"overrides": {"hedge_coverage": 5}}).status_code == 422
    assert client.post(f"{BASE}/modules/fx/scenario", json={"unknown_field": 1}).status_code == 422
    assert client.post(f"{BASE}/modules/fx/scenario", json={"shocks": [99]}).json()["detail"].startswith("FX shock")


def test_compound_endpoint(client):
    r = client.post(f"{BASE}/compound", json={}).json()
    assert r["compound"]["overlap_adjustment"]["amount_usd_m"] == -24.0 and set(r["components"]) == {"fx", "interest", "bunker", "freight"}


def test_review_recomputes_server_side_and_logs_versions(client, service):
    r = client.post(f"{BASE}/modules/bunker/reviews", json={"reviewer": "Jenna", "comment": "checked",
                    "request": {"overrides": {"consumption_mt": 2.0}}})
    assert r.status_code == 201
    snap = client.get(f"{BASE}/snapshots/{r.json()['snapshot_id']}").json()
    assert snap["payload"]["headline"]["impact"]["value"] == -80.0           # 50 x 2.0 x 0.8
    entry = client.get(f"{BASE}/audit", params={"module": "bunker"}).json()["entries"][0]
    d = entry["details"]
    assert entry["actor"] == "Jenna" and entry["comment"] == "checked"
    assert d["formula_version"] == "formulas-1.0" and d["threshold_version"] == "demo-thresholds-1.0"
    assert d["assumption_overrides"] == {"consumption_mt": 2.0} and "inputs" in d
    assert client.post(f"{BASE}/modules/bunker/reviews", json={"reviewer": "", "request": {}}).status_code == 422


def test_confirmed_trigger_is_audited(client):
    body = {"reviewer": "A", "request": {"confirmed_triggers": [{"kind": "payment_shortfall", "confirmed_by": "Head of Treasury", "evidence": "Funding call 31 Oct"}]}}
    r = client.post(f"{BASE}/modules/fx/reviews", json=body).json()
    assert r["status"] == "CRITICAL"
    d = client.get(f"{BASE}/audit").json()["entries"][0]["details"]
    assert d["confirmed_triggers"][0]["confirmed_by"] == "Head of Treasury"


def test_no_route_can_execute_a_transaction(client):
    spec = client.get("/openapi.json").json()["paths"]
    paths = [p.lower() for p in spec if p != "/healthz"]
    assert paths, "expected the sensitivity routes in the OpenAPI document"
    for word in ("execute", "trade", "pay", "borrow", "hedge/", "order", "submit", "transfer"):
        assert not any(word in p for p in paths), word
    assert {m for path, item in spec.items() if "/data-sources" not in path for m in item} <= {"get", "post"}
    # the only mutating verbs are the replace/restore of input DATA; none of them can move money
    assert {m for path, item in spec.items() if "/data-sources" in path for m in item} <= {"get", "put", "delete"}
    posts = sorted(p for p, item in spec.items() if "post" in item)
    assert all(p.endswith(("/scenario", "/reset", "/reviews", "/compound", "/dashboard")) for p in posts)   # calculations and audit records only


def test_bearer_token_when_configured(service):
    c = TestClient(create_app(service, token="s3cret"))
    assert c.get("/healthz").status_code == 200
    assert c.get(f"{BASE}/overview").status_code == 401
    assert c.get(f"{BASE}/overview", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get(f"{BASE}/overview", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_wildcard_cors_refused(service):
    with pytest.raises(ValueError):
        create_app(service, allowed_origins=["*"])


def test_audit_is_append_only_and_chain_detects_tampering(tmp_path):
    path = tmp_path / "audit.sqlite"
    store = AuditStore(path)
    store.append("review", module="fx", actor="a", comment="c")
    store.append("review", module="fx", actor="b")
    assert store.verify_chain() == (True, None)
    with pytest.raises(sqlite3.DatabaseError):
        store.conn.execute("UPDATE audit_log SET comment='edited' WHERE seq=1")
    store.conn.rollback()
    with pytest.raises(sqlite3.DatabaseError):
        store.conn.execute("DELETE FROM audit_log")
    store.conn.rollback()
    # tamper outside the application by dropping the trigger, then edit
    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER log_no_update")
    raw.execute("UPDATE audit_log SET comment='edited' WHERE seq=1")
    raw.commit()
    assert AuditStore(path).verify_chain() == (False, 1)


def test_router_mounts_into_another_app(service):
    """The future unified dashboard API can include the sensitivity router next to the news routes."""
    from fastapi import FastAPI
    from treasury_sensitivity.api import create_router
    app = FastAPI()
    app.include_router(create_router(service))
    assert TestClient(app).get(f"{BASE}/overview").status_code == 200
