# Claude Code Project Instructions --- Local AI Incident Management System

## 1. Project Overview

This repository is a learning-first, fully local, zero-paid-API
AI-powered Incident Management / AI-SRE platform.

The system must be developed incrementally from an empty machine state.
Do NOT assume that Kubernetes, Prometheus, Grafana, Alertmanager,
LangChain, LangGraph, Chroma, PostgreSQL, n8n, React, or other project
components are already installed.

Current known environment: - Windows 11 host - WSL2 - Docker Desktop -
Ollama - NVIDIA RTX 4070 Super 12 GB VRAM - 32 GB RAM - Intel
i7-12700F - Project is being developed primarily from WSL/terminal -
Kind is being used for the local Kubernetes cluster - A Kind cluster
named `incident-ai` is the intended cluster name

The goal is to create a portfolio-quality local AI incident management
system that can:

1.  Run a deliberately breakable application in local Kubernetes.
2.  Monitor it with Prometheus.
3.  Visualize metrics with Grafana.
4.  Generate alerts through Prometheus/Alertmanager.
5.  Send incidents to a FastAPI backend.
6.  Investigate Kubernetes state, logs, events, metrics, and recent
    Git/deployment changes.
7.  Search internal runbooks and previous incidents through local RAG.
8.  Use Ollama/local LLMs for reasoning.
9.  Use LangChain for model/tool/RAG integration.
10. Use LangGraph for the stateful incident-investigation workflow.
11. Produce structured RCA/resolution/rollback recommendations.
12. Require human approval before destructive remediation.
13. Store incident history in PostgreSQL.
14. Use n8n for surrounding automation.
15. Provide a React dashboard and incident chat UI.
16. Learn from previous incidents through incident-history retrieval.

------------------------------------------------------------------------

# 2. Core Architecture

Final target architecture:

``` text
                         USER
                           |
                           v
                    +--------------+
                    | React UI     |
                    | Dashboard    |
                    | Chat         |
                    +------+-------+
                           |
                           v
                    +--------------+
                    | FastAPI      |
                    | Incident API |
                    +------+-------+
                           |
                           v
                    +--------------+
                    | LangGraph    |
                    | AI Workflow  |
                    +------+-------+
                           |
        +------------------+------------------+
        |                  |                  |
        v                  v                  v
 Kubernetes Tools   Prometheus Tools     Git Tools
        |                  |                  |
        +------------------+------------------+
                           |
                           v
                    +--------------+
                    | RAG          |
                    | Chroma       |
                    +------+-------+
                           |
                           v
                    +--------------+
                    | Ollama       |
                    | Local LLM    |
                    +------+-------+
                           |
                           v
                    +--------------+
                    | Structured   |
                    | Incident RCA |
                    +------+-------+
                           |
                           v
                    Human Approval
                           |
                 +---------+---------+
                 |                   |
              Approve              Reject
                 |
                 v
             Remediation
                 |
                 v
            Kubernetes


Kubernetes
    |
    +--> Prometheus
             |
             +--> Grafana
             |
             +--> Alertmanager
                       |
                       v
                    FastAPI


n8n
 |
 +--> notifications
 +--> Jira/Slack integrations
 +--> report automation
 +--> workflow automation
```

------------------------------------------------------------------------

# 3. Important Architectural Decisions

## 3.1 Keep Ollama outside Kubernetes initially

Ollama should run directly on Windows because the host GPU is available
there.

Initial architecture:

``` text
Windows
|
+-- Ollama
|     |
|     +-- Local LLM
|
+-- Docker Desktop
      |
      +-- Kind Kubernetes
            |
            +-- incident-demo
            +-- Prometheus
            +-- Grafana
            +-- Alertmanager
```

Do not unnecessarily put Ollama inside Kubernetes during the initial
implementation.

The goal is to keep GPU access simple and preserve VRAM for inference.

## 3.2 Use Kind for Kubernetes

Use:

``` bash
kind create cluster --name incident-ai
```

Do not switch to Minikube, k3d, or another Kubernetes distribution
unless there is a concrete technical reason.

## 3.3 Build before adding complexity

Do not install all technologies simultaneously.

The implementation must progress through working milestones.

## 3.4 No paid cloud APIs

Do not introduce: - OpenAI API - Azure OpenAI - AWS Bedrock - paid
embedding APIs - cloud vector databases - paid observability services

The project must work locally.

If an external API is suggested as an optional enhancement, clearly
label it as OPTIONAL and do not make the core project depend on it.

## 3.5 Human approval before destructive actions

The AI must NOT automatically execute arbitrary: - `kubectl delete` -
`kubectl rollout undo` - `kubectl scale` - configuration changes - shell
commands - production-like destructive operations

The default workflow is:

``` text
AI recommendation
       |
       v
Human approval
       |
       v
Controlled remediation tool
```

------------------------------------------------------------------------

# 4. Development Philosophy

This is a learning project as well as a portfolio project.

When implementing anything:

1.  Explain WHY the component exists.
2.  Explain WHAT it does.
3.  Explain HOW it connects to the rest of the system.
4.  Give a small verification test.
5.  Only then move to the next component.
6.  Do not hide setup behind unexplained automation.
7.  Prefer simple, explicit implementations over unnecessary
    abstraction.
8.  Do not introduce a framework merely because it is popular.
9.  Keep each milestone independently testable.

The user is learning AI/ML engineering from a DevOps/SRE background.
Explanations should connect new AI concepts to familiar DevOps concepts.

Example:

Instead of saying:

> "LangGraph maintains state."

Explain:

> "Think of LangGraph state like the incident context object that gets
> passed between investigation stages. Each node adds evidence, similar
> to an SRE investigation accumulating logs, metrics, deployment
> information, and runbook findings."

------------------------------------------------------------------------

# 5. Implementation Milestones

## Milestone 0 --- Local development environment

Verify:

``` bash
docker --version
kubectl version --client
kind version
helm version
git --version
python3 --version
ollama --version
```

Do not proceed if Docker is unavailable.

Expected project directory:

``` text
incident-ai/
├── k8s/
├── demo-app/
├── monitoring/
├── backend/
├── agent/
├── rag/
├── knowledge/
├── frontend/
├── n8n/
├── scripts/
└── README.md
```

------------------------------------------------------------------------

# 6. Milestone 1 --- Kubernetes

Create:

``` bash
kind create cluster --name incident-ai
```

Verify:

``` bash
kubectl get nodes
kubectl get pods -A
kubectl config current-context
```

Expected context:

``` text
kind-incident-ai
```

Expected node status:

``` text
Ready
```

If Kubernetes is not available, do not continue to application
deployment.

------------------------------------------------------------------------

# 7. Milestone 2 --- Breakable Demo Application

Create a small application called:

``` text
incident-demo
```

The application must expose:

``` text
/
 /health
 /api/order
 /metrics
```

The app should: - return healthy status normally - expose Prometheus
metrics - generate request/error metrics - support a controlled failure
mode - produce useful structured logs - have a version identifier -
support deployment v1 and v2

Example controlled failure mechanism:

``` text
FAIL_MODE=true
```

When enabled, selected endpoints should fail.

The purpose is to create deterministic incidents for testing.

The application should be containerized.

Kubernetes resources:

``` text
Deployment
Service
ConfigMap
```

Potential future resources:

``` text
ServiceMonitor
PrometheusRule
```

------------------------------------------------------------------------

# 8. Milestone 3 --- Reproducible Incident

Normal:

``` text
incident-demo
  |
  +--> /health = healthy
  +--> /api/order = success
```

Failure:

``` bash
kubectl set env deployment/incident-demo FAIL_MODE=true
```

The system should produce a reproducible incident such as:

``` text
HTTP 500
```

or an application error.

Verify manually:

``` bash
kubectl get pods
kubectl logs deployment/incident-demo
kubectl describe pod <pod>
```

This establishes ground truth.

The AI must eventually diagnose an incident whose correct root cause is
known.

------------------------------------------------------------------------

# 9. Milestone 4 --- Prometheus

Install the Prometheus stack using Helm.

Preferred stack:

``` text
kube-prometheus-stack
```

Components include: - Prometheus - Grafana - Alertmanager -
kube-state-metrics - node exporter - Prometheus Operator

Do not manually install dozens of Kubernetes monitoring components
unless required.

The application must expose metrics that allow detection of:

``` text
HTTP request rate
HTTP error rate
latency
application health
```

------------------------------------------------------------------------

# 10. Milestone 5 --- Grafana

Grafana is the human observability interface.

Create dashboards showing:

``` text
Application requests
HTTP 5xx rate
Latency
CPU
Memory
Pod status
```

Grafana is NOT the AI engine.

Its purpose is:

``` text
Human -> visual understanding of system state
```

The AI will use Prometheus APIs for machine-readable evidence.

------------------------------------------------------------------------

# 11. Milestone 6 --- Prometheus Alerting + Alertmanager

Create an alert such as:

``` text
HighErrorRate
```

Example logic:

``` text
HTTP 5xx rate > threshold
FOR 30 seconds
```

Flow:

``` text
Application
   |
   v
Prometheus
   |
   v
Alert
   |
   v
Alertmanager
```

Alertmanager should eventually call:

``` text
POST /webhook/alert
```

on FastAPI.

Verify by intentionally breaking the application.

------------------------------------------------------------------------

# 12. Milestone 7 --- FastAPI

Create backend:

``` text
backend/
```

Use: - Python - FastAPI - Uvicorn - Pydantic

Initial endpoints:

``` text
GET  /health
POST /webhook/alert
GET  /incidents
GET  /incidents/{incident_id}
POST /incidents/{incident_id}/approve
POST /incidents/{incident_id}/reject
POST /chat
```

First milestone:

``` text
Alertmanager
     |
     v
FastAPI
     |
     v
log received incident
```

Do not add LangGraph yet.

------------------------------------------------------------------------

# 13. Milestone 8 --- Kubernetes Investigation Tools

Create Python tools/functions that can retrieve:

``` text
get_pods()
get_pod_logs()
get_pod_events()
get_deployment()
get_deployment_history()
get_services()
get_config()
```

Prefer the official Kubernetes Python client rather than shelling out to
arbitrary shell commands.

The tool layer should return structured data.

Example:

``` json
{
  "pod": "incident-demo-123",
  "status": "Running",
  "restarts": 3,
  "logs": "...",
  "events": []
}
```

The AI should not directly execute arbitrary shell commands.

------------------------------------------------------------------------

# 14. Milestone 9 --- Prometheus Investigation Tools

Create tools:

``` text
query_prometheus()
get_error_rate()
get_latency()
get_cpu_usage()
get_memory_usage()
get_request_rate()
```

The tools should support a time window around an incident.

Example:

``` text
incident_time - 10 minutes
to
incident_time + current
```

The system should collect historical evidence rather than only current
values.

------------------------------------------------------------------------

# 15. Milestone 10 --- Git / Deployment Intelligence

The demo application must have a Git repository.

Use Git history to determine:

``` text
current version
previous version
recent commits
recent diff
deployment timestamp
image tag
```

The AI should eventually answer:

``` text
What changed immediately before the incident?
```

This is a core SRE capability.

Do not fake deployment history.

------------------------------------------------------------------------

# 16. Milestone 11 --- Local RAG

Create:

``` text
knowledge/
├── runbooks/
├── architecture/
├── troubleshooting/
└── incidents/
```

Example documents:

``` text
crashloopbackoff.md
high-error-rate.md
database-errors.md
deployment-failure.md
kubernetes.md
incident-response.md
```

The RAG pipeline:

``` text
Documents
   |
   v
Parsing
   |
   v
Chunking
   |
   v
Embeddings
   |
   v
Chroma
```

Use local embeddings.

No OpenAI/Azure embedding API.

------------------------------------------------------------------------

# 17. Milestone 12 --- Chroma

Use Chroma as the local vector database.

Persist its data to disk.

The application should be able to:

``` text
add documents
search documents
retrieve top-k chunks
```

The RAG retriever should return:

``` text
document
source
chunk
similarity/relevance information
metadata
```

Every retrieved document must preserve its source filename.

This is important for explainability.

------------------------------------------------------------------------

# 18. Milestone 13 --- Ollama

Use the already installed Ollama.

Initial local model:

``` text
qwen2.5-coder:7b
```

Do not automatically download huge models.

The machine has:

``` text
RTX 4070 Super 12GB
```

so model selection should respect VRAM/RAM limitations.

The LLM must be accessed through Ollama locally.

No cloud model should be required.

------------------------------------------------------------------------

# 19. Milestone 14 --- LangChain

Use LangChain only where it provides value.

Responsibilities:

``` text
LLM integration
tool integration
retriever integration
prompt construction
structured output
```

Do not make LangChain responsible for the entire application.

The actual incident workflow belongs to LangGraph.

------------------------------------------------------------------------

# 20. Milestone 15 --- LangGraph Incident Agent

Build a stateful incident workflow.

Target graph:

``` text
START
  |
  v
Receive Alert
  |
  v
Identify Service
  |
  v
Collect Kubernetes Evidence
  |
  v
Collect Prometheus Evidence
  |
  v
Check Recent Deployment
  |
  v
Search RAG
  |
  v
Analyze Evidence
  |
  v
Root Cause
  |
  v
Generate Resolution
  |
  v
Assess Rollback
  |
  v
Human Approval
  |
  +---- Reject ---> END
  |
 Approve
  |
  v
Controlled Remediation
  |
  v
Verify
  |
  v
Store Incident
  |
  v
END
```

The graph should maintain a typed incident state.

Example:

``` python
IncidentState:
    incident_id
    alert
    service
    namespace
    kubernetes_evidence
    metrics_evidence
    deployment_evidence
    rag_evidence
    analysis
    root_cause
    confidence
    recommended_actions
    rollback_recommended
    approval_status
    remediation_result
```

------------------------------------------------------------------------

# 21. Milestone 16 --- Structured Incident Report

Use Pydantic.

Target schema:

``` text
IncidentReport
├── incident_id
├── severity
├── service
├── namespace
├── summary
├── root_cause
├── evidence[]
├── confidence
├── recommended_actions[]
├── rollback_recommended
├── rollback_reason
└── status
```

The LLM must produce structured output.

Do not rely on parsing arbitrary natural-language responses with regex.

------------------------------------------------------------------------

# 22. Evidence-Based RCA

The AI must distinguish:

``` text
FACT
```

from:

``` text
INFERENCE
```

For example:

``` text
Facts:
- HTTP 500 increased to 43%.
- Deployment v2 occurred 3 minutes before alert.
- Logs contain database connection failures.

Inference:
- Deployment v2 likely introduced the database configuration problem.

Confidence:
0.91
```

Never claim certainty when the evidence is insufficient.

------------------------------------------------------------------------

# 23. Milestone 17 --- Human-in-the-Loop Remediation

Default safety model:

``` text
AI recommends
      |
      v
Human approves
      |
      v
Controlled tool
      |
      v
Kubernetes
```

Never give the LLM unrestricted shell access.

Implement explicit remediation functions:

``` text
rollback_deployment()
restart_deployment()
scale_deployment()
```

Each function should: - validate inputs - restrict
namespaces/resources - log the action - require explicit approval -
return structured result

For this local project, destructive operations are allowed only inside
the dedicated demo namespace/cluster.

------------------------------------------------------------------------

# 24. PostgreSQL Incident History

Use PostgreSQL for structured incident history.

Store:

``` text
incident_id
created_at
closed_at
alert_name
service
namespace
severity
root_cause
confidence
evidence
resolution
rollback
status
```

Do not store only AI text.

Keep structured columns plus JSON fields where useful.

------------------------------------------------------------------------

# 25. Incident Memory / RAG Feedback Loop

After an incident closes:

``` text
Incident
+
Root Cause
+
Evidence
+
Resolution
```

should be converted into a searchable incident document.

Future incident:

``` text
New Incident
   |
   v
RAG search
   |
   v
Similar previous incidents
```

The AI can then use historical incidents as evidence.

Do not claim the model "learned" in the ML-training sense.

This is retrieval-based incident memory.

------------------------------------------------------------------------

# 26. n8n

Introduce n8n only after the core incident engine works.

Use n8n for:

``` text
notifications
report generation
Slack
Jira
email
scheduled cleanup
workflow automation
```

n8n should not replace LangGraph as the incident reasoning engine.

------------------------------------------------------------------------

# 27. React Frontend

Build the UI after the backend and agent are functional.

Screens:

## Dashboard

Show: - active incidents - resolved incidents - severity - affected
service - status

## Incident Details

Show: - alert - timeline - metrics - logs - deployment - RAG sources -
RCA - confidence - recommended actions - rollback recommendation

## Chat

Allow questions such as:

``` text
Why did this incident happen?
What changed before the incident?
Show the evidence.
Have we seen this before?
What should I do?
```

## Approval

Show:

``` text
Recommendation
Evidence
Risk
Expected impact

[Approve]
[Reject]
```

------------------------------------------------------------------------

# 28. API Boundaries

Keep these boundaries:

``` text
React
  |
  v
FastAPI
  |
  v
Incident Service
  |
  +--> LangGraph
  |
  +--> Kubernetes Tools
  |
  +--> Prometheus Tools
  |
  +--> Git Tools
  |
  +--> RAG
  |
  +--> PostgreSQL
  |
  +--> Ollama
```

Do not let React directly communicate with Kubernetes.

Do not let the LLM directly communicate with Kubernetes.

The backend/tool layer must enforce permissions and safety.

------------------------------------------------------------------------

# 29. Testing Strategy

Every milestone must have a test.

Examples:

## Kubernetes

``` bash
kubectl get nodes
```

## Demo app

``` bash
curl /health
```

## Failure

``` bash
kubectl set env deployment/incident-demo FAIL_MODE=true
```

## Logs

``` bash
kubectl logs ...
```

## Prometheus

Verify metric exists.

## Alertmanager

Trigger test alert.

## FastAPI

Send sample webhook.

## Kubernetes tool

Call `get_pods()`.

## Prometheus tool

Query error rate.

## RAG

Ask:

``` text
How do we troubleshoot database connection failures?
```

## Ollama

Run local inference.

## LangGraph

Run a synthetic incident.

## Full system

``` text
break application
    |
    v
Prometheus detects
    |
    v
Alertmanager fires
    |
    v
FastAPI receives
    |
    v
LangGraph investigates
    |
    v
Kubernetes + Prometheus + Git + RAG
    |
    v
Ollama analyzes
    |
    v
RCA
    |
    v
Human approval
    |
    v
Remediation
    |
    v
Verify
    |
    v
Store incident
```

------------------------------------------------------------------------

# 30. Definition of Done

The project is considered complete only when this end-to-end scenario
works:

1.  `incident-demo` is healthy.
2.  Prometheus collects its metrics.
3.  Grafana displays them.
4.  A Prometheus rule detects high error rate.
5.  Alertmanager receives the alert.
6.  Alertmanager calls FastAPI.
7.  FastAPI creates an incident.
8.  LangGraph starts investigation.
9.  Kubernetes tools collect pod status/logs/events.
10. Prometheus tools collect metrics around the incident.
11. Git tools identify recent deployment/change.
12. RAG retrieves relevant runbooks.
13. RAG retrieves similar historical incidents.
14. Ollama analyzes the evidence locally.
15. The system produces structured RCA.
16. The system gives a confidence score.
17. The system recommends resolution.
18. The system determines whether rollback is justified.
19. Human approves/rejects remediation.
20. Controlled remediation executes if approved.
21. System verifies the result.
22. Incident is stored in PostgreSQL.
23. Incident becomes available for future RAG retrieval.
24. React displays the entire investigation.
25. n8n can automate surrounding notifications/workflows.

------------------------------------------------------------------------

# 31. What Claude Code Should NOT Do

Do not:

-   install everything automatically without explanation
-   overwrite existing Docker/WSL configuration without asking
-   delete Docker volumes unnecessarily
-   delete Kubernetes clusters without explicit confirmation
-   modify Windows networking unnecessarily
-   expose Kubernetes APIs to the internet
-   expose Grafana/Prometheus publicly
-   install cloud dependencies for the core system
-   use paid APIs
-   give an LLM unrestricted shell access
-   run arbitrary commands generated by the LLM
-   hard-code secrets
-   commit `.env` files
-   commit kubeconfigs or credentials
-   skip verification steps
-   move to the next milestone when the current milestone is broken

------------------------------------------------------------------------

# 32. Safety Rules

This is a local lab.

Use a dedicated namespace:

``` text
incident-lab
```

All automated remediation should be restricted to:

``` text
namespace = incident-lab
```

The agent must reject remediation outside that namespace.

Never implement:

``` text
kubectl delete namespace ...
```

as an AI tool.

Prefer narrowly scoped operations:

``` text
rollback specific deployment
restart specific deployment
scale specific deployment
```

------------------------------------------------------------------------

# 33. Code Quality

Prefer:

-   Python type hints
-   Pydantic models
-   async FastAPI where appropriate
-   structured logging
-   clear module boundaries
-   `.env.example`
-   Dockerfiles
-   Docker Compose where appropriate
-   Kubernetes manifests
-   Helm values files
-   unit tests
-   integration tests
-   README documentation

Avoid: - giant Python files - hard-coded configuration - global mutable
state - duplicated prompts - arbitrary shell execution - unnecessary
abstractions

------------------------------------------------------------------------

# 34. Recommended Repository Structure

Final target:

``` text
incident-ai/
│
├── CLAUDE.md
├── README.md
├── .gitignore
├── .env.example
│
├── demo-app/
│   ├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── tests/
│
├── k8s/
│   ├── namespace.yaml
│   ├── demo-app/
│   ├── monitoring/
│   └── alerts/
│
├── monitoring/
│   ├── prometheus/
│   ├── grafana/
│   └── alertmanager/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── services/
│   │   ├── tools/
│   │   └── main.py
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
│
├── agent/
│   ├── graph/
│   ├── nodes/
│   ├── state/
│   ├── prompts/
│   └── tools/
│
├── rag/
│   ├── ingestion/
│   ├── embeddings/
│   ├── retrieval/
│   └── chroma/
│
├── knowledge/
│   ├── runbooks/
│   ├── architecture/
│   ├── troubleshooting/
│   └── incidents/
│
├── database/
│   ├── migrations/
│   └── models/
│
├── frontend/
│
├── n8n/
│   └── workflows/
│
├── scripts/
│
└── tests/
    ├── integration/
    └── e2e/
```

------------------------------------------------------------------------

# 35. How Claude Code Should Work With Me

When I ask for the next step:

1.  Inspect the current repository/state first.
2.  Determine which milestone we are currently on.
3.  Do not assume previous steps succeeded.
4.  Verify prerequisites.
5.  Explain the purpose briefly.
6.  Make the smallest necessary change.
7.  Run a verification test.
8.  Show me the expected result.
9.  Tell me what the successful result means.
10. Stop before moving to the next major milestone unless I explicitly
    ask to continue.

If a command fails: - diagnose the failure - explain the cause - provide
the correction - verify again - do not work around the problem blindly

When installing a dependency, explain: - why it is needed - where it
will run - what depends on it - how we will verify it

------------------------------------------------------------------------

# 36. Current Starting Point

At the time this CLAUDE.md was created, the user has:

``` text
Windows 11
WSL2
Docker Desktop
Ollama
```

Kind has been installed in WSL.

The intended command is:

``` bash
kind create cluster --name incident-ai
```

The previous `kubectl get nodes` failed with:

``` text
The connection to the server localhost:8080 was refused
```

This happened because a Kubernetes cluster/context had not yet been
created.

The immediate next task is:

``` text
1. Verify Docker
2. Verify Kind
3. Create Kind cluster
4. Verify kubectl context
5. Verify Kubernetes nodes
6. Stop
```

Do NOT proceed to Prometheus/Grafana/application deployment until
Kubernetes is confirmed healthy.

------------------------------------------------------------------------

# 37. Immediate Commands

``` bash
docker ps
kind version
kubectl version --client
kind get clusters
```

If no cluster exists:

``` bash
kind create cluster --name incident-ai
```

Then:

``` bash
kubectl get nodes
kubectl get pods -A
kubectl config current-context
```

Expected:

``` text
kind-incident-ai
```

and node status:

``` text
Ready
```

------------------------------------------------------------------------

# 38. Overall Learning Order

The user should learn the technologies in this order:

``` text
Docker
  ↓
Kubernetes
  ↓
Containerized application
  ↓
Prometheus
  ↓
Grafana
  ↓
Alertmanager
  ↓
FastAPI
  ↓
Kubernetes API/tools
  ↓
Prometheus API/tools
  ↓
Git/deployment intelligence
  ↓
Embeddings
  ↓
Chroma
  ↓
RAG
  ↓
Ollama
  ↓
LangChain
  ↓
LangGraph
  ↓
Structured output/Pydantic
  ↓
Human-in-the-loop
  ↓
PostgreSQL
  ↓
React
  ↓
n8n
  ↓
Incident memory
```

The learning approach is deliberately project-driven rather than
theory-first.

------------------------------------------------------------------------

# 39. Final Objective

The final portfolio story should be:

> "I built a completely local AI-powered incident management platform
> that detects Kubernetes incidents through Prometheus, automatically
> gathers Kubernetes logs/events/metrics and deployment history,
> retrieves relevant operational knowledge using RAG, uses a local LLM
> through Ollama to perform evidence-based root-cause analysis,
> generates structured resolution and rollback recommendations, requires
> human approval for remediation, verifies the result, and stores the
> incident for future retrieval."

The system should be demonstrable on a local machine without requiring a
cloud account or paid AI API.
