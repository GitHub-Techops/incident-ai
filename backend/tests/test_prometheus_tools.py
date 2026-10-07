"""Tests for the Prometheus tools and the metrics evidence collector."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.evidence import collect_metrics_evidence
from app.tools.errors import InvalidTargetError, NamespaceNotAllowedError
from app.tools.prometheus import (
    MAX_WINDOW, PrometheusError, PrometheusTools, choose_step, summarize,
)
from app.models.evidence import MetricPoint

NS, APP = "incident-lab", "incident-demo"
INCIDENT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
START = INCIDENT - timedelta(minutes=10)
END = INCIDENT + timedelta(minutes=5)


def matrix(*series: tuple[dict, list[tuple[datetime, str]]]) -> dict:
    return {"resultType": "matrix", "result": [
        {"metric": labels, "values": [[t.timestamp(), v] for t, v in values]} for labels, values in series
    ]}


def error_ratio_values() -> list[tuple[datetime, str]]:
    """0% for the first 8 minutes, 60% from one minute before the alert on."""
    values = []
    t = START
    while t <= END:
        values.append((t, "0.6" if t >= INCIDENT - timedelta(minutes=1) else "0"))
        t += timedelta(seconds=15)
    return values


class FakeClient:
    """Answers query_range calls from a dict of {query substring: data or exception}."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.queries: list[dict] = []

    def get(self, path, params):
        self.queries.append(params)
        for fragment, response in self.responses.items():
            if fragment in params["query"]:
                if isinstance(response, Exception):
                    raise response
                return response
        return matrix()


def make_tools(responses=None) -> tuple[PrometheusTools, FakeClient]:
    client = FakeClient(responses or {})
    return PrometheusTools(client, allowed_namespaces=frozenset({NS})), client


# --- safety ------------------------------------------------------------------

@pytest.mark.parametrize("service", ['x"} or vector(1) #', "Incident-Demo", "a.b", ""])
def test_rejects_names_that_could_inject_promql(service):
    tools, client = make_tools()
    with pytest.raises(InvalidTargetError):
        tools.get_error_rate(NS, service, START, END)
    assert client.queries == []


def test_rejects_namespace_outside_allow_list():
    tools, client = make_tools()
    with pytest.raises(NamespaceNotAllowedError):
        tools.get_request_rate("kube-system", APP, START, END)
    assert client.queries == []


def test_rejects_too_long_window():
    tools, _ = make_tools()
    with pytest.raises(ValueError):
        tools.query_prometheus("up", START, START + MAX_WINDOW + timedelta(seconds=1))


# --- parsing and summaries ---------------------------------------------------

def test_choose_step_never_finer_than_scrape_interval():
    assert choose_step(START, START + timedelta(minutes=15)) == 15
    assert choose_step(START, START + timedelta(hours=6)) == 90  # 21600 s / 240 points


def test_nan_and_inf_become_none():
    tools, _ = make_tools({"up": matrix(({}, [(START, "NaN"), (INCIDENT, "+Inf"), (END, "1")]))})
    series = tools.query_prometheus("up", START, END)
    assert [p.v for p in series[0].points] == [None, None, 1.0]


def test_summary_compares_baseline_with_incident():
    points = [MetricPoint(t=t, v=float(v)) for t, v in error_ratio_values()]
    s = summarize(points, INCIDENT)
    assert s.baseline_avg == 0.0  # the 2 minutes before the alert are excluded
    assert s.incident_avg == 0.6
    assert s.peak == 0.6
    assert s.peak_at == INCIDENT - timedelta(minutes=1)
    assert s.last == 0.6


def test_summary_splits_incident_and_recovery_when_resolved():
    resolved = INCIDENT + timedelta(minutes=2)
    points = [MetricPoint(t=t, v=0.6 if INCIDENT - timedelta(minutes=1) <= t <= resolved else 0.0)
              for t, _ in error_ratio_values()]
    s = summarize(points, INCIDENT, resolved)
    assert s.baseline_avg == 0.0
    assert s.incident_avg == 0.6  # recovery points no longer dilute it
    assert s.recovery_avg == 0.0
    assert s.last == 0.0


def test_summary_has_no_recovery_while_open():
    points = [MetricPoint(t=t, v=float(v)) for t, v in error_ratio_values()]
    assert summarize(points, INCIDENT).recovery_avg is None


def test_error_rate_query_and_result():
    tools, client = make_tools({"status=~": matrix(({}, error_ratio_values()))})
    result = tools.get_error_rate(NS, APP, START, END, INCIDENT)

    assert result.name == "error_rate"
    assert result.unit == "ratio"
    assert 'namespace="incident-lab", job="incident-demo"' in result.query
    assert result.series[0].summary.incident_avg == 0.6
    assert client.queries[0]["step"] == "15"


def test_prometheus_error_is_raised():
    tools, _ = make_tools({"status=~": PrometheusError("bad_data: parse error")})
    with pytest.raises(PrometheusError):
        tools.get_error_rate(NS, APP, START, END)


# --- collector ---------------------------------------------------------------

def test_collector_gathers_all_metrics_and_records_failures():
    tools, client = make_tools({
        "status=~": matrix(({}, error_ratio_values())),
        "container_memory": PrometheusError("cannot reach Prometheus"),
    })
    evidence = collect_metrics_evidence(tools, NS, APP, INCIDENT, timedelta(minutes=10), END)

    assert [m.name for m in evidence.metrics] == ["request_rate", "error_rate", "latency_p95", "cpu_usage"]
    assert evidence.errors == ["memory_usage: cannot reach Prometheus"]
    assert evidence.window_start == START
    assert evidence.window_end == END
    assert len(client.queries) == 5


def test_collector_caps_window_for_long_incidents():
    tools, _ = make_tools()
    evidence = collect_metrics_evidence(tools, NS, APP, INCIDENT, timedelta(minutes=10),
                                        end=INCIDENT + timedelta(days=2))
    assert evidence.window_end - evidence.window_start == MAX_WINDOW
    assert evidence.errors == []
