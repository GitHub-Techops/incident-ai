---
title: Reading metrics during an incident
type: troubleshooting
services: [incident-demo]
alerts: [HighErrorRate]
tags: [prometheus, promql, grafana, metrics, latency, error rate]
---

# Reading metrics during an incident

## Where to look

- Grafana dashboard `incident-demo`: request rate, 5xx rate, latency, CPU,
  memory, pod status and running version, for humans.
- Prometheus (`http://localhost:9090` through `scripts/port-forward.sh`) for ad-hoc
  PromQL.
- The incident backend's metrics evidence, which queries Prometheus over a
  window from 10 minutes before the alert until its recovery and summarizes
  each series as baseline, incident and recovery averages.

## The application metrics

| Metric | Meaning |
|---|---|
| `http_requests_total{method, path, status}` | Requests served, by endpoint and status code |
| `http_request_duration_seconds` | Latency histogram, by endpoint |
| `app_info{version}` | Which version is running (value is always 1) |

Unknown paths are recorded as `path="other"` to keep the number of series
bounded.

## Useful queries

Error ratio (what the `HighErrorRate` alert uses):

```promql
sum(rate(http_requests_total{namespace="incident-lab", job="incident-demo", status=~"5.."}[1m]))
  /
sum(rate(http_requests_total{namespace="incident-lab", job="incident-demo"}[1m]))
```

Errors by endpoint:

```promql
sum by (path, status) (rate(http_requests_total{job="incident-demo", status=~"5.."}[1m]))
```

p95 latency by endpoint:

```promql
histogram_quantile(0.95,
  sum by (le, path) (rate(http_request_duration_seconds_bucket{job="incident-demo"}[1m])))
```

Running versions (more than one means a rollout or a split):

```promql
count by (version) (app_info{job="incident-demo"})
```

## Interpreting the numbers

- Compare with the baseline before the incident, not with an absolute target.
  Prometheus alerts fire only after the condition held for the `for:` duration,
  so the minutes just before the alert are already abnormal.
- The error ratio counts all endpoints. If one endpoint takes 70% of the traffic
  and fails completely, the ratio is about 70% even though other endpoints work.
- Errors with higher latency: requests wait (timeouts, slow dependency) before
  failing. Errors with unchanged or lower latency: requests fail fast.
- CPU and memory are per pod. After a rollout the pods have new names, so the
  series start fresh and have no baseline of their own.
- `rate()` over `[1m]` needs a minute of data: right after a rollout or a fix
  the graph lags reality by up to a minute.
