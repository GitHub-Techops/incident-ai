# incident-ai

A fully local AI-SRE lab: a deliberately breakable service on Kubernetes,
monitored with Prometheus, and an incident backend that investigates alerts on
its own. The end goal is a local LLM (Ollama) that writes evidence-based root
cause analyses, with human approval before any remediation. No cloud account
and no paid APIs.

> **Status: Milestone 10 of 17.** Detection, alerting and automatic evidence
> collection (Kubernetes, Prometheus, Git/deployments) work end to end.
> The AI part (RAG, Ollama, LangGraph) is next. Nothing here is an LLM yet:
> this is the evidence layer the LLM will reason over.

## What works today

```
 deploy v2 / FAIL_MODE=true
           |
           v
 incident-demo (Kubernetes, ns incident-lab) --metrics--> Prometheus --> Grafana
                                                             |
                                                  HighErrorRate (>5% 5xx for 30s)
                                                             v
                                                       Alertmanager
                                                             | webhook
                                                             v
                                            incident-backend (FastAPI, ns incident-ai)
                                              creates an incident, then collects:
                                    +-------------------+-------------------+
                                    |                   |                   |
                              Kubernetes tools    Prometheus tools      Git tools
                              pods, logs, events  error rate, latency,  revision -> commit,
                              rollout history,    CPU, memory: before/  commits + diff since
                              config (read-only)  during/after alert    the previous deploy
```

Example: deploying a buggy v2 release. About a minute later the backend has opened an
incident, and its deployment evidence says:

```
FACT: revision 25 (incident-demo:v2, commit cd1ffef) was deployed 1m17s before the alert,
      replacing revision 24 (incident-demo:v1, commit 8b8c46c).
FACT: Kubernetes changes in revision 25: image incident-demo:v1 -> incident-demo:v2;
      git commit: 8b8c46c -> cd1ffef.
FACT: Code change from 8b8c46c to cd1ffef in demo-app/: 2 commit(s), 2 file(s) changed, +28 -4.
FACT: Commit cd1ffef: demo-app: make the orders DB host and port configurable
```

The Kubernetes evidence adds 188 error log lines (`database connection refused`,
`db_host: orders-db:5433`). The metrics evidence shows the error rate going
0% → 50.6% → 0% across the alert. Pods stayed Ready the whole time, because
`/health` is shallow on purpose.

## Design choices

- **Evidence before AI.** Every tool returns structured, typed facts (Pydantic)
  with their source: the exact PromQL query, the ReplicaSet, the commit SHA. A
  later LLM claim can be traced back to a field.
- **Read-only by default.** The backend's ServiceAccount can only `get`/`list`
  in `incident-lab` (RBAC). An app-level namespace allow-list rejects other
  namespaces before any API call is made.
- **No shell for anything.** The Kubernetes tools use the official Python client.
  Git runs without a shell, accepts only commit SHAs, and passes them after
  `--end-of-options`. The Prometheus tools validate names, so values can't inject
  PromQL.
- **Real deployment history.** `scripts/deploy-demo.sh` builds the image from
  a Git tag (`git archive`, so image == commit) and records the commit on the
  pod template. Every rollout revision says which code it ran.
- **Metrics around the incident, not just now.** Each series is summarized as
  baseline / incident / recovery averages, plus its peak.
- **Nothing exposed.** UIs are reached only through `kubectl port-forward` on
  localhost. Grafana's password is a generated Secret and never touches Git.

## Stack

Kind (Kubernetes v1.37) · kube-prometheus-stack (Prometheus, Grafana,
Alertmanager) · Python 3.12, FastAPI, Pydantic · official `kubernetes` client ·
Docker. Planned: Chroma, Ollama (`qwen2.5-coder:7b`), LangChain, LangGraph,
PostgreSQL, React, n8n.

## Repository layout

```
demo-app/            the breakable service (FastAPI, Prometheus metrics, JSON logs)
backend/             incident backend: API, evidence collectors, tools, tests
  app/tools/         kubernetes.py, prometheus.py, git.py (the investigation tools)
k8s/                 kind config, namespaces, demo app, ServiceMonitor, alert rules, backend
monitoring/          Helm values (Prometheus/Alertmanager routing), Grafana dashboard
scripts/             deploy-demo.sh, sync-git-mirror.sh, port-forward.sh, create-grafana-secret.sh
```

## Run it

Prerequisites: Docker, kind, kubectl, helm, git, Python 3.12.

```bash
# Cluster and namespaces
kind create cluster --config k8s/kind-config.yaml
kubectl apply -f k8s/namespace.yaml

# Monitoring
./scripts/create-grafana-secret.sh
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack --version 92.0.0 \
  -n monitoring -f monitoring/prometheus/values.yaml
kubectl apply -k monitoring/grafana/

# Demo app (v1) and its monitoring
docker build -t incident-demo:v1 demo-app && kind load docker-image incident-demo:v1 --name incident-ai
kubectl apply -f k8s/demo-app/ -f k8s/monitoring/ -f k8s/alerts/

# Backend
docker build --build-arg APP_VERSION=0.4.0 -t incident-backend:0.4.0 backend
kind load docker-image incident-backend:0.4.0 --name incident-ai
kubectl apply -f k8s/backend/

# Deploy v1 from its Git tag (records the commit), then open the UIs
scripts/deploy-demo.sh v1
./scripts/port-forward.sh     # Grafana :3000, Prometheus :9090, Alertmanager :9093
```

## Break it and investigate

```bash
scripts/deploy-demo.sh v2                 # buggy release: alert fires in ~1 min
# or: kubectl set env deployment/incident-demo -n incident-lab FAIL_MODE=true

kubectl port-forward -n incident-ai svc/incident-backend 8080:80
curl -s "localhost:8080/incidents?status=open"
curl -s -X POST localhost:8080/incidents/<ID>/evidence/kubernetes
curl -s -X POST localhost:8080/incidents/<ID>/evidence/metrics
curl -s -X POST localhost:8080/incidents/<ID>/evidence/deployment

scripts/deploy-demo.sh v1                 # fix: the incident resolves ~1 min later
```

Tests: `cd backend && pytest -q` (63 tests: API, Kubernetes tools against a fake
API, Prometheus tools, and Git tools against a real throwaway repository).

## Roadmap

- [x] M0–M1 Local environment, Kind cluster
- [x] M2–M3 Breakable demo app, reproducible incident
- [x] M4–M6 Prometheus, Grafana dashboard, HighErrorRate alert → Alertmanager
- [x] M7 FastAPI incident backend (Alertmanager webhook)
- [x] M8 Kubernetes investigation tools (read-only RBAC)
- [x] M9 Prometheus investigation tools (time window around the incident)
- [x] M10 Git / deployment intelligence
- [ ] M11–M12 Runbooks + local RAG with Chroma
- [ ] M13–M14 Ollama + LangChain
- [ ] M15–M16 LangGraph investigation agent, structured RCA with confidence
- [ ] M17 Human-approved remediation (rollback / restart / scale, `incident-lab` only)
- [ ] PostgreSQL incident history, incident memory, React UI, n8n
