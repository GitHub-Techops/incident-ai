"""Evidence JSON (as the backend returns it) -> compact text for the model."""

from tools.evidence_format import MAX_DIFF_CHARS, format_deployment, format_kubernetes, format_metrics

ERR = '{"level": "ERROR", "msg": "order failed", "error": "database connection refused", "db_host": "orders-db:5433"}'


def k8s_evidence() -> dict:
    return {
        "collected_at": "2026-10-14T10:00:00Z",
        "pods": [{"name": "incident-demo-abc-1", "phase": "Running", "ready": True, "restarts": 0,
                  "containers": [{"name": "incident-demo", "state": "running", "reason": None,
                                  "last_termination_reason": None}]}],
        "logs": [{"pod": "incident-demo-abc-1", "previous": False, "line_count": 100, "error_line_count": 40,
                  "error_lines": [f'{{"level": "ERROR", "msg": "request", "n": {i}}}' for i in range(10)] + [ERR, ERR]}],
        "events": [{"type": "Normal", "reason": "Pulled", "message": "ok", "object_kind": "Pod",
                    "object_name": "p", "count": 1, "last_seen": "t1"},
                   {"type": "Warning", "reason": "Unhealthy", "message": "probe failed", "object_kind": "Pod",
                    "object_name": "p", "count": 3, "last_seen": "t0"}],
        "deployment": {"name": "incident-demo", "revision": "25", "ready_replicas": 2, "desired_replicas": 2,
                       "images": ["incident-demo:v2"], "env": {}, "env_from": ["configmap/incident-demo-config"],
                       "conditions": ["Available=True"]},
        "deployment_history": [{"revision": 25, "current": True, "created_at": "t", "images": ["incident-demo:v2"],
                                "git_commit": "cd1ffef" + "0" * 33,
                                "changes_from_previous": ["image: incident-demo:v1 -> incident-demo:v2"]}],
        "services": [{"name": "incident-demo", "ready_endpoints": 2, "not_ready_endpoints": 0}],
        "configs": [{"name": "incident-demo-config", "data": {"FAIL_MODE": "false"}}],
        "errors": [],
    }


def test_kubernetes_keeps_what_an_sre_reads_first():
    text = format_kubernetes(k8s_evidence())
    assert "incident-demo-abc-1: Running, ready=True, restarts=0" in text
    assert "40 error lines out of 100" in text
    assert text.count("database connection refused") == 1  # duplicates removed, most recent first
    assert text.index("Warning Unhealthy") < text.index("Normal Pulled")  # warnings first
    assert "revision 25 (current)" in text and "commit cd1ffef" in text
    assert "image: incident-demo:v1 -> incident-demo:v2" in text
    assert "{'FAIL_MODE': 'false'}" in text


def test_metrics_are_summarized_with_units():
    ev = {"window_start": "a", "window_end": "b", "incident_time": "c", "resolved_time": None, "errors": [],
          "metrics": [
              {"name": "error_rate", "description": "5xx share", "unit": "ratio", "series": [
                  {"labels": {}, "summary": {"baseline_avg": 0.0, "incident_avg": 0.506, "recovery_avg": None,
                                             "peak": 0.67, "peak_at": "t"}, "points": [{"t": "x", "v": 1}] * 500}]},
              {"name": "latency_p95", "description": "p95", "unit": "seconds", "series": [
                  {"labels": {"path": "/api/order"}, "summary": {"baseline_avg": 0.048, "incident_avg": 0.436,
                                                                 "recovery_avg": 0.049, "peak": 0.5, "peak_at": "t"}}]},
          ]}
    text = format_metrics(ev)
    assert "[all] baseline 0.0%, incident 50.6%, peak 67.0%" in text
    assert "[path=/api/order] baseline 48 ms, incident 436 ms, recovery 49 ms" in text
    assert "points" not in text


def test_deployment_facts_and_truncated_diff():
    ev = {"facts": ["revision 25 was deployed 1m17s before the alert."], "errors": [],
          "diff": {"from_sha": "8b8c46c" + "0" * 33, "to_sha": "cd1ffef" + "0" * 33, "path": "demo-app",
                   "patch": "+x\n" * MAX_DIFF_CHARS}}
    text = format_deployment(ev)
    assert "- revision 25 was deployed 1m17s before the alert." in text
    assert "Diff 8b8c46c..cd1ffef in demo-app/" in text
    assert "(diff truncated)" in text
