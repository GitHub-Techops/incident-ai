"""API tests: run with `pytest` from backend/."""

import copy
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_k8s_tools, get_prometheus_tools
from app.logging_config import LOGGER_NAME, JsonFormatter
from app.main import create_app
from app.tools.prometheus import PrometheusTools
from tests.fake_k8s import FakeCore, make_tools, pod
from tests.test_prometheus_tools import FakeClient as FakePrometheusClient
from tests.test_prometheus_tools import error_ratio_values as prom_error_ratio_values
from tests.test_prometheus_tools import matrix as prom_matrix

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> TestClient:
    # Fresh app (and fresh in-memory store) for every test.
    return TestClient(create_app())


@pytest.fixture
def firing() -> dict:
    return json.loads((FIXTURES / "alertmanager_firing.json").read_text())


def resolved_from(firing: dict) -> dict:
    payload = copy.deepcopy(firing)
    payload["status"] = "resolved"
    payload["alerts"][0]["status"] = "resolved"
    payload["alerts"][0]["endsAt"] = "2026-10-07T08:31:00Z"
    return payload


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_firing_alert_creates_incident(client, firing):
    r = client.post("/webhook/alert", json=firing)
    assert r.status_code == 200
    created = r.json()["created"]
    assert len(created) == 1

    incident = client.get(f"/incidents/{created[0]}").json()
    assert incident["alert_name"] == "HighErrorRate"
    assert incident["service"] == "incident-demo"
    assert incident["namespace"] == "incident-lab"
    assert incident["severity"] == "critical"
    assert incident["status"] == "open"
    assert incident["approval_status"] == "not_required"


def test_repeated_firing_alert_updates_same_incident(client, firing):
    first = client.post("/webhook/alert", json=firing).json()
    second = client.post("/webhook/alert", json=firing).json()
    assert second["created"] == []
    assert second["updated"] == first["created"]
    assert len(client.get("/incidents").json()) == 1


def test_resolved_alert_resolves_incident(client, firing):
    incident_id = client.post("/webhook/alert", json=firing).json()["created"][0]
    r = client.post("/webhook/alert", json=resolved_from(firing))
    assert r.json()["resolved"] == [incident_id]

    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["status"] == "resolved"
    assert incident["resolved_at"].startswith("2026-10-07T08:31:00")


def test_alert_firing_again_after_resolve_opens_new_incident(client, firing):
    first = client.post("/webhook/alert", json=firing).json()["created"][0]
    client.post("/webhook/alert", json=resolved_from(firing))
    second = client.post("/webhook/alert", json=firing).json()["created"][0]
    assert second != first


def test_resolved_alert_without_open_incident_is_ignored(client, firing):
    r = client.post("/webhook/alert", json=resolved_from(firing))
    assert r.json()["ignored"] == 1


def test_list_filters_by_status(client, firing):
    client.post("/webhook/alert", json=firing)
    assert len(client.get("/incidents?status=open").json()) == 1
    assert client.get("/incidents?status=resolved").json() == []


def test_unknown_incident_returns_404(client):
    assert client.get("/incidents/INC-nope").status_code == 404


def test_approve_without_pending_remediation_returns_409(client, firing):
    incident_id = client.post("/webhook/alert", json=firing).json()["created"][0]
    r = client.post(f"/incidents/{incident_id}/approve", json={"decided_by": "sre"})
    assert r.status_code == 409


def test_invalid_payload_returns_422(client):
    assert client.post("/webhook/alert", json={"hello": "world"}).status_code == 422


def test_webhook_log_line_keeps_backend_service_name(client, firing):
    records: list[logging.LogRecord] = []
    capture = logging.Handler()
    capture.emit = records.append
    log = logging.getLogger(LOGGER_NAME)
    log.addHandler(capture)
    try:
        client.post("/webhook/alert", json=firing)
    finally:
        log.removeHandler(capture)

    record = next(r for r in records if r.getMessage() == "alert created")
    entry = json.loads(JsonFormatter().format(record))
    assert entry["service"] == "incident-backend"
    assert entry["affected_service"] == "incident-demo"


def _with_fake_k8s(client: TestClient, tools) -> TestClient:
    client.app.dependency_overrides[get_k8s_tools] = lambda: tools
    return client


def test_kubernetes_evidence_endpoint_attaches_evidence(client, firing):
    core = FakeCore(pods=[pod("incident-demo-p1")], logs='{"level": "ERROR", "msg": "order failed"}')
    _with_fake_k8s(client, make_tools(core=core))
    incident_id = client.post("/webhook/alert", json=firing).json()["created"][0]

    r = client.post(f"/incidents/{incident_id}/evidence/kubernetes")
    assert r.status_code == 200
    assert r.json()["pods"][0]["name"] == "incident-demo-p1"

    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["kubernetes_evidence"]["logs"][0]["error_line_count"] == 1
    assert "Kubernetes evidence collected" in incident["timeline"][-1]["event"]


def test_kubernetes_evidence_forbidden_outside_allowed_namespace(client, firing):
    core = FakeCore()
    _with_fake_k8s(client, make_tools(core=core))
    firing["alerts"][0]["labels"]["namespace"] = "kube-system"
    incident_id = client.post("/webhook/alert", json=firing).json()["created"][0]

    r = client.post(f"/incidents/{incident_id}/evidence/kubernetes")
    assert r.status_code == 403
    assert core.calls == []


def test_metrics_evidence_endpoint_uses_alert_time_window(client, firing):
    fake = FakePrometheusClient({"status=~": prom_matrix(({}, prom_error_ratio_values()))})
    client.app.dependency_overrides[get_prometheus_tools] = lambda: PrometheusTools(
        fake, frozenset({"incident-lab"}))
    incident_id = client.post("/webhook/alert", json=firing).json()["created"][0]

    r = client.post(f"/incidents/{incident_id}/evidence/metrics")
    assert r.status_code == 200
    body = r.json()
    # Window starts 10 minutes before the alert's startsAt (fixture: 08:30:00.628Z).
    assert body["window_start"].startswith("2026-10-07T08:20:00")
    assert [m["name"] for m in body["metrics"]] == [
        "request_rate", "error_rate", "latency_p95", "cpu_usage", "memory_usage"]

    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["metrics_evidence"] is not None
    assert "Prometheus evidence collected" in incident["timeline"][-1]["event"]


def test_chat_not_implemented_yet(client):
    assert client.post("/chat", json={"message": "why?"}).status_code == 501
