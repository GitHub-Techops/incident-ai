"""Tests for the read-only Kubernetes tools and the evidence collector."""

import json

import pytest
from kubernetes import client as k

from app.services.evidence import collect_kubernetes_evidence
from app.tools.kubernetes import NamespaceNotAllowedError, is_error_line
from tests.fake_k8s import (
    APP, NS, T0, T1, FakeApps, FakeCore, FakeDiscovery, deployment, event, make_tools, pod, replicaset,
)

FAIL_ENV = [k.V1EnvVar(name="FAIL_MODE", value="true")]


def json_line(level: str, msg: str, **fields) -> str:
    return json.dumps({"level": level, "msg": msg, **fields})


# --- namespace guard -------------------------------------------------------

def test_rejects_namespace_outside_allow_list_before_calling_api():
    core = FakeCore(pods=[pod("p1")])
    tools = make_tools(core=core)
    with pytest.raises(NamespaceNotAllowedError):
        tools.get_pods("kube-system")
    assert core.calls == []  # no API call was made


def test_collector_rejects_disallowed_namespace():
    with pytest.raises(NamespaceNotAllowedError):
        collect_kubernetes_evidence(make_tools(), "monitoring", APP)


# --- pods ------------------------------------------------------------------

def test_get_pods_reports_readiness_restarts_and_crash_reasons():
    core = FakeCore(pods=[
        pod("p-ok"),
        pod("p-crash", ready=False, restarts=4, waiting="CrashLoopBackOff", last_terminated="OOMKilled"),
    ])
    pods = {p.name: p for p in make_tools(core=core).get_pods(NS)}

    assert pods["p-ok"].ready is True
    assert pods["p-ok"].restarts == 0
    assert pods["p-ok"].owner == f"{APP}-rs2"

    crash = pods["p-crash"]
    assert crash.ready is False
    assert crash.restarts == 4
    assert crash.containers[0].state == "waiting"
    assert crash.containers[0].reason == "CrashLoopBackOff"
    assert crash.containers[0].last_termination_reason == "OOMKilled"


# --- logs ------------------------------------------------------------------

def test_is_error_line_trusts_json_level_and_scans_plain_text():
    assert is_error_line(json_line("ERROR", "order failed"))
    # JSON info line that merely mentions "error" is not an error.
    assert not is_error_line(json_line("INFO", "request", note="no error here"))
    assert is_error_line("ERROR:    Exception in ASGI application")
    assert is_error_line("psycopg.OperationalError: connection refused")
    assert not is_error_line("INFO:     Uvicorn running on http://0.0.0.0:8000")


def test_get_pod_logs_extracts_error_lines():
    logs = "\n".join([
        json_line("INFO", "request", status=200),
        json_line("ERROR", "order failed", error="database connection refused"),
        json_line("ERROR", "request", status=500),
    ])
    result = make_tools(core=FakeCore(logs=logs)).get_pod_logs(NS, "p1", APP)
    assert result.line_count == 3
    assert result.error_line_count == 2
    assert "database connection refused" in result.error_lines[0]


def test_get_pod_logs_truncates_huge_logs_keeping_the_end():
    logs = "x" * 30_000 + "\nLAST LINE"
    result = make_tools(core=FakeCore(logs=logs)).get_pod_logs(NS, "p1")
    assert result.truncated is True
    assert result.logs.endswith("LAST LINE")
    assert len(result.logs) == 20_000


# --- deployment + history -----------------------------------------------------

def test_get_deployment_shows_env_and_redacts_secrets():
    env = [
        k.V1EnvVar(name="FAIL_MODE", value="true"),
        k.V1EnvVar(name="DB_PASSWORD", value="hunter2"),
        k.V1EnvVar(name="API_TOKEN", value_from=k.V1EnvVarSource(
            secret_key_ref=k.V1SecretKeySelector(name="api", key="token"))),
    ]
    info = make_tools(apps=FakeApps(deployment=deployment(env))).get_deployment(NS, APP)

    assert info.env["FAIL_MODE"] == "true"
    assert info.env["DB_PASSWORD"] == "<redacted>"
    assert info.env["API_TOKEN"] == "<from secret api/token>"
    assert "hunter2" not in info.model_dump_json()
    assert info.env_from == [f"configmap/{APP}-config"]
    assert info.revision == "2"
    assert info.ready_replicas == 2


def test_deployment_history_shows_what_changed_newest_first():
    apps = FakeApps(
        deployment=deployment(FAIL_ENV, revision="2"),
        replicasets=[
            replicaset(f"{APP}-rs1", "1", env=[], replicas=0),
            replicaset(f"{APP}-rs2", "2", env=FAIL_ENV, replicas=2),
            replicaset("someone-else", "7", env=[], replicas=1, owner_uid="other"),  # not ours
        ],
    )
    history = make_tools(apps=apps).get_deployment_history(NS, APP)

    assert [r.revision for r in history] == [2, 1]
    assert history[0].current is True
    assert history[0].changes_from_previous == ["env FAIL_MODE: <unset> -> true"]
    assert history[1].current is False
    assert history[1].changes_from_previous == []


# --- events / services / config ------------------------------------------------

def test_get_events_filters_by_prefix_and_sorts_newest_first():
    core = FakeCore(events=[
        event(f"{APP}-p1", "Started", "Started container", T0),
        event(f"{APP}-p1", "Unhealthy", "Readiness probe failed", T1, type_="Warning"),
        event("other-app-x", "Started", "Started container", T1),
        event(f"{APP}-p2", "Scheduled", "no timestamp", None),
    ])
    events = make_tools(core=core).get_events(NS, name_prefix=APP)
    assert [e.reason for e in events] == ["Unhealthy", "Started", "Scheduled"]


def test_get_services_counts_ready_endpoints():
    svc = k.V1Service(
        metadata=k.V1ObjectMeta(name=APP),
        spec=k.V1ServiceSpec(type="ClusterIP", cluster_ip="10.96.0.10", selector={"app": APP},
                             ports=[k.V1ServicePort(name="http", port=80, target_port="http", protocol="TCP")]),
    )
    endpoint_slice = k.V1EndpointSlice(address_type="IPv4", endpoints=[
        k.V1Endpoint(addresses=["10.244.0.9"], conditions=k.V1EndpointConditions(ready=True)),
        k.V1Endpoint(addresses=["10.244.0.10"], conditions=k.V1EndpointConditions(ready=False)),
        k.V1Endpoint(addresses=["10.244.0.11"]),  # unset condition counts as ready
    ])
    services = make_tools(core=FakeCore(services=[svc]),
                          discovery=FakeDiscovery([endpoint_slice])).get_services(NS)
    assert services[0].ready_endpoints == 2
    assert services[0].not_ready_endpoints == 1
    assert services[0].ports == ["http:80->http/TCP"]


def test_get_config_skips_cluster_ca_and_redacts_secret_keys():
    core = FakeCore(config_maps=[
        k.V1ConfigMap(metadata=k.V1ObjectMeta(name="kube-root-ca.crt"), data={"ca.crt": "..."}),
        k.V1ConfigMap(metadata=k.V1ObjectMeta(name=f"{APP}-config"),
                      data={"FAIL_MODE": "false", "API_KEY": "abc123"}),
    ])
    configs = make_tools(core=core).get_config(NS)
    assert [c.name for c in configs] == [f"{APP}-config"]
    assert configs[0].data == {"FAIL_MODE": "false", "API_KEY": "<redacted>"}


# --- collector -------------------------------------------------------------

def test_collector_gathers_everything_and_reads_previous_logs_after_restarts():
    core = FakeCore(
        pods=[pod(f"{APP}-p1", restarts=1)],
        logs=json_line("ERROR", "order failed", error="database connection refused"),
    )
    apps = FakeApps(deployment=deployment(FAIL_ENV),
                    replicasets=[replicaset(f"{APP}-rs2", "2", env=FAIL_ENV, replicas=2)])
    evidence = collect_kubernetes_evidence(make_tools(core=core, apps=apps), NS, APP)

    assert len(evidence.pods) == 1
    assert [l.previous for l in evidence.logs] == [False, True]
    assert evidence.deployment.env["FAIL_MODE"] == "true"
    assert evidence.deployment_history[0].revision == 2
    assert evidence.errors == []


def test_collector_records_failed_tools_and_keeps_going():
    # No deployment exists: deployment tools fail with 404, the rest still runs.
    core = FakeCore(pods=[pod(f"{APP}-p1")], logs="ok")
    evidence = collect_kubernetes_evidence(make_tools(core=core, apps=FakeApps(deployment=None)), NS, APP)

    assert evidence.deployment is None
    assert len(evidence.pods) == 1
    assert len(evidence.logs) == 1
    assert evidence.errors == [
        "get_deployment: HTTP 404 Not Found",
        "get_deployment_history: HTTP 404 Not Found",
    ]
