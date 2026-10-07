"""Read-only Prometheus investigation tools.

Answer "what did the metrics do around the incident?" over a time window
(by default 10 minutes before the alert until now), not just "what is the
value right now?". Each result keeps its exact PromQL so every number can be
re-run and checked.

Safety: Prometheus has no per-namespace permissions. Anyone who can query it
can read every metric in the cluster, so Kubernetes RBAC doesn't help here.
The helpers therefore only build queries for allowed namespaces, and namespace
and service names must be valid Kubernetes names, so nothing can be injected
into a query. query_prometheus() takes raw PromQL and is meant for internal
use: don't hand it to an LLM unrestricted.

Uses Prometheus' HTTP API (query_range) with the standard library only.
"""

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

from app.models.evidence import MetricPoint, MetricResult, MetricSeries, SeriesSummary
from app.tools.errors import InvalidTargetError, NamespaceNotAllowedError

# Kubernetes object names (RFC 1123 labels): also guarantees no quotes, braces or
# regex characters can reach the PromQL strings below.
K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

MIN_STEP_SECONDS = 15  # the scrape interval; a finer step adds no information
MAX_POINTS = 240  # per series; keeps responses (and later LLM prompts) small
MAX_WINDOW = timedelta(hours=6)
# Excluded from the baseline: the alert only fires after its condition held for
# a while, so the minutes just before it are already abnormal.
BASELINE_GAP = timedelta(minutes=2)


class PrometheusError(RuntimeError):
    pass


def choose_step(start: datetime, end: datetime) -> int:
    seconds = (end - start).total_seconds()
    return max(MIN_STEP_SECONDS, math.ceil(seconds / MAX_POINTS))


def _to_float(raw: str) -> float | None:
    value = float(raw)  # Prometheus sends numbers as strings, incl. "NaN" and "+Inf"
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, 6)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def summarize(points: list[MetricPoint], incident_time: datetime | None,
              resolved_time: datetime | None = None) -> SeriesSummary:
    known = [(p.t, p.v) for p in points if p.v is not None]
    if not known:
        return SeriesSummary()
    peak_at, peak = max(known, key=lambda tv: tv[1])
    summary = SeriesSummary(peak=peak, peak_at=peak_at, last=known[-1][1])
    if incident_time is not None:
        summary.baseline_avg = _avg([v for t, v in known if t < incident_time - BASELINE_GAP])
        summary.incident_avg = _avg([v for t, v in known
                                     if t >= incident_time and (resolved_time is None or t <= resolved_time)])
        if resolved_time is not None:
            summary.recovery_avg = _avg([v for t, v in known if t > resolved_time])
    return summary


class PrometheusClient:
    """Thin HTTP client for the Prometheus API."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            # Bad queries come back as HTTP 400/422 with a JSON error body.
            try:
                body = json.load(exc)
            except ValueError:
                raise PrometheusError(f"HTTP {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PrometheusError(f"cannot reach Prometheus at {self.base_url}: {reason}") from exc
        if body.get("status") != "success":
            raise PrometheusError(f"{body.get('errorType', 'error')}: {body.get('error', 'unknown error')}")
        return body["data"]


class PrometheusTools:
    def __init__(self, client: PrometheusClient, allowed_namespaces: frozenset[str]) -> None:
        self.client = client
        self.allowed_namespaces = allowed_namespaces

    def check_target(self, namespace: str, service: str) -> None:
        for value in (namespace, service):
            if not K8S_NAME.match(value):
                raise InvalidTargetError(f"'{value}' is not a valid Kubernetes name")
        if namespace not in self.allowed_namespaces:
            raise NamespaceNotAllowedError(
                f"Namespace '{namespace}' is not allowed; allowed: {sorted(self.allowed_namespaces)}"
            )

    def query_prometheus(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step_seconds: int | None = None,
        incident_time: datetime | None = None,
        resolved_time: datetime | None = None,
    ) -> list[MetricSeries]:
        """Run a PromQL range query; one MetricSeries per returned time series."""
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > MAX_WINDOW:
            raise ValueError(f"window is longer than {MAX_WINDOW}")
        step = step_seconds or choose_step(start, end)
        data = self.client.get("/api/v1/query_range", {
            "query": query,
            "start": f"{start.timestamp():.3f}",
            "end": f"{end.timestamp():.3f}",
            "step": str(step),
        })
        if data.get("resultType") != "matrix":
            raise PrometheusError(f"expected a matrix result, got {data.get('resultType')}")

        series = []
        for item in data["result"]:
            points = [
                MetricPoint(t=datetime.fromtimestamp(float(ts), UTC), v=_to_float(value))
                for ts, value in item["values"]
            ]
            labels = {k: v for k, v in item["metric"].items() if k != "__name__"}
            series.append(MetricSeries(labels=labels, points=points,
                                       summary=summarize(points, incident_time, resolved_time)))
        return series

    def _metric(self, name: str, description: str, unit: str, query: str, start: datetime,
                end: datetime, incident_time: datetime | None, resolved_time: datetime | None) -> MetricResult:
        return MetricResult(
            name=name, description=description, unit=unit, query=query,
            series=self.query_prometheus(query, start, end, incident_time=incident_time,
                                         resolved_time=resolved_time),
        )

    # --- per-service helpers (selectors use the ServiceMonitor's job=<service>) ---

    def get_request_rate(self, namespace: str, service: str, start: datetime, end: datetime,
                         incident_time: datetime | None = None,
                 resolved_time: datetime | None = None) -> MetricResult:
        self.check_target(namespace, service)
        query = (f'sum by (status) (rate(http_requests_total{{namespace="{namespace}", '
                 f'job="{service}"}}[1m]))')
        return self._metric("request_rate", "HTTP requests per second, by status code",
                            "requests/s", query, start, end, incident_time, resolved_time)

    def get_error_rate(self, namespace: str, service: str, start: datetime, end: datetime,
                       incident_time: datetime | None = None,
                 resolved_time: datetime | None = None) -> MetricResult:
        self.check_target(namespace, service)
        selector = f'namespace="{namespace}", job="{service}"'
        # `or vector(0)`: with no 5xx at all the numerator is empty; show 0 instead of nothing.
        query = (f'(sum(rate(http_requests_total{{{selector}, status=~"5.."}}[1m])) or vector(0)) '
                 f'/ sum(rate(http_requests_total{{{selector}}}[1m]))')
        return self._metric("error_rate", "Share of HTTP requests answered with 5xx (0.25 = 25%)",
                            "ratio", query, start, end, incident_time, resolved_time)

    def get_latency(self, namespace: str, service: str, start: datetime, end: datetime,
                    incident_time: datetime | None = None, resolved_time: datetime | None = None,
                    quantile: float = 0.95) -> MetricResult:
        self.check_target(namespace, service)
        if not 0 < quantile < 1:
            raise ValueError("quantile must be between 0 and 1")
        query = (f'histogram_quantile({quantile}, sum by (le, path) (rate('
                 f'http_request_duration_seconds_bucket{{namespace="{namespace}", job="{service}"}}[1m])))')
        return self._metric(f"latency_p{round(quantile * 100)}",
                            f"p{round(quantile * 100)} request latency by path",
                            "seconds", query, start, end, incident_time, resolved_time)

    def get_cpu_usage(self, namespace: str, service: str, start: datetime, end: datetime,
                      incident_time: datetime | None = None,
                 resolved_time: datetime | None = None) -> MetricResult:
        self.check_target(namespace, service)
        # Container metrics (cAdvisor) have no job=<service>; match pods by name prefix.
        query = (f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{namespace}", '
                 f'pod=~"{service}-.*", container!="", container!="POD"}}[1m]))')
        return self._metric("cpu_usage", "CPU used per pod", "cores", query, start, end, incident_time, resolved_time)

    def get_memory_usage(self, namespace: str, service: str, start: datetime, end: datetime,
                         incident_time: datetime | None = None,
                 resolved_time: datetime | None = None) -> MetricResult:
        self.check_target(namespace, service)
        query = (f'sum by (pod) (container_memory_working_set_bytes{{namespace="{namespace}", '
                 f'pod=~"{service}-.*", container!="", container!="POD"}})')
        return self._metric("memory_usage", "Memory working set per pod (what the OOM killer looks at)",
                            "bytes", query, start, end, incident_time, resolved_time)
