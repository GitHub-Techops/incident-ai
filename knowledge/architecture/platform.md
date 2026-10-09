---
title: incident-ai platform architecture
type: architecture
services: [incident-backend, incident-demo]
alerts: [HighErrorRate]
tags: [platform, kubernetes, prometheus, alertmanager, backend, rbac, namespaces]
---

# incident-ai platform architecture

## Overview

A local Kubernetes cluster (kind, `kind-incident-ai`) runs the lab
application, the monitoring stack and the incident backend:

| Namespace | Contents |
|---|---|
| `incident-lab` | `incident-demo` and `traffic-generator`: the systems that break |
| `monitoring` | Prometheus, Alertmanager, Grafana, kube-state-metrics, node exporter |
| `incident-ai` | `incident-backend`: receives alerts and investigates incidents |

## Alert flow

1. Prometheus scrapes `incident-demo`'s `/metrics` every 15 seconds
   (ServiceMonitor).
2. The `HighErrorRate` rule fires after the 5xx ratio stays above 5% for 30
   seconds.
3. Alertmanager routes alerts from `namespace="incident-lab"` to the backend's
   webhook, waiting 10 seconds to group them, and sends a resolved notice when
   the alert stops.
4. The backend creates one incident per alert fingerprint and resolves it when
   the alert resolves.

Expect about one minute from the start of the failures to the incident being
created, and one to two minutes from the fix to the incident resolving.

## Incident backend

FastAPI service collecting evidence for an incident:

- Kubernetes evidence: pods, logs, events, deployment and rollout history,
  services, ConfigMaps.
- Metrics evidence: request rate, error rate, p95 latency, CPU and memory
  around the incident.
- Deployment evidence: the revision running at alert time, the one before it,
  and the Git commits and diff between them.

Incidents are kept in memory: restarting the backend clears them.

## Permissions and safety

- The backend's ServiceAccount can only `get`/`list` pods, pod logs, events,
  deployments, ReplicaSets, services, EndpointSlices and ConfigMaps in
  `incident-lab`. It cannot read Secrets, exec into pods or change anything.
- The tools reject other namespaces before calling any API.
- Remediation (rollback, restart, scale) requires human approval and is limited
  to `incident-lab`.
- Nothing is exposed outside the machine: UIs are reached with
  `kubectl port-forward` on localhost.
