"""Incident read and approval endpoints."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_git_tools, get_k8s_tools, get_prometheus_tools, get_store
from app.logging_config import LOGGER_NAME
from app.models.evidence import DeploymentEvidence, KubernetesEvidence, MetricsEvidence
from app.models.incident import ApprovalRequest, ApprovalStatus, Incident, IncidentStatus
from app.services.evidence import (
    collect_deployment_evidence, collect_kubernetes_evidence, collect_metrics_evidence,
)
from app.services.incident_store import IncidentStore
from app.tools.errors import InvalidTargetError, NamespaceNotAllowedError
from app.tools.git import GitTools
from app.tools.kubernetes import KubernetesTools
from app.tools.prometheus import PrometheusTools

# After an incident resolves, keep this much "recovery" in the metrics window.
RECOVERY_WINDOW = timedelta(minutes=5)

router = APIRouter(prefix="/incidents", tags=["incidents"])
log = logging.getLogger(LOGGER_NAME)


def _get_or_404(store: IncidentStore, incident_id: str) -> Incident:
    incident = store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident


@router.get("", response_model=list[Incident])
async def list_incidents(
    status: IncidentStatus | None = None, store: IncidentStore = Depends(get_store)
) -> list[Incident]:
    return store.list_incidents(status)


@router.get("/{incident_id}", response_model=Incident)
async def get_incident(incident_id: str, store: IncidentStore = Depends(get_store)) -> Incident:
    return _get_or_404(store, incident_id)


@router.post("/{incident_id}/evidence/kubernetes", response_model=KubernetesEvidence)
async def collect_k8s_evidence(
    incident_id: str,
    store: IncidentStore = Depends(get_store),
    tools: KubernetesTools = Depends(get_k8s_tools),
) -> KubernetesEvidence:
    """Collect pods, logs, events, deployment + history, services and config for
    the incident's service, attach it to the incident, and return it."""
    incident = _get_or_404(store, incident_id)
    if not incident.namespace or not incident.service:
        raise HTTPException(status_code=422, detail="Incident has no namespace/service labels to investigate")
    try:
        # The Kubernetes client is blocking; run it off the event loop.
        evidence = await run_in_threadpool(
            collect_kubernetes_evidence, tools, incident.namespace, incident.service
        )
    except NamespaceNotAllowedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.attach_kubernetes_evidence(incident_id, evidence)
    return evidence


def _incident_time(incident: Incident) -> datetime:
    """When the alert started firing (Alertmanager's startsAt), else when we created the incident."""
    starts_at = incident.alert.get("startsAt")
    return datetime.fromisoformat(starts_at) if starts_at else incident.created_at


@router.post("/{incident_id}/evidence/metrics", response_model=MetricsEvidence)
async def collect_metrics(
    incident_id: str,
    request: Request,
    store: IncidentStore = Depends(get_store),
    tools: PrometheusTools = Depends(get_prometheus_tools),
) -> MetricsEvidence:
    """Request rate, error rate, p95 latency, CPU and memory from `lookback` minutes
    before the alert until now (or 5 minutes after resolution), with baseline vs
    incident summaries. Attaches it to the incident and returns it."""
    incident = _get_or_404(store, incident_id)
    if not incident.namespace or not incident.service:
        raise HTTPException(status_code=422, detail="Incident has no namespace/service labels to investigate")

    end = datetime.now(UTC)
    if incident.resolved_at is not None:
        end = min(end, incident.resolved_at + RECOVERY_WINDOW)
    lookback = timedelta(minutes=request.app.state.settings.metrics_lookback_minutes)
    try:
        evidence = await run_in_threadpool(
            collect_metrics_evidence, tools, incident.namespace, incident.service,
            _incident_time(incident), lookback, end, incident.resolved_at,
        )
    except NamespaceNotAllowedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.attach_metrics_evidence(incident_id, evidence)
    return evidence


@router.post("/{incident_id}/evidence/deployment", response_model=DeploymentEvidence)
async def collect_deployment(
    incident_id: str,
    store: IncidentStore = Depends(get_store),
    k8s: KubernetesTools = Depends(get_k8s_tools),
    git: GitTools = Depends(get_git_tools),
) -> DeploymentEvidence:
    """What changed immediately before the incident: the deployment revision
    running when the alert fired, the one it replaced, and the Git commits and
    diff between them. Attaches it to the incident and returns it."""
    incident = _get_or_404(store, incident_id)
    if not incident.namespace or not incident.service:
        raise HTTPException(status_code=422, detail="Incident has no namespace/service labels to investigate")
    try:
        evidence = await run_in_threadpool(
            collect_deployment_evidence, k8s, git, incident.namespace, incident.service, _incident_time(incident),
        )
    except NamespaceNotAllowedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.attach_deployment_evidence(incident_id, evidence)
    return evidence


async def _decide(incident_id: str, approved: bool, body: ApprovalRequest, store: IncidentStore) -> Incident:
    incident = _get_or_404(store, incident_id)
    # Nothing recommends a remediation until the agent exists (Milestone 15+),
    # so approval_status is never PENDING yet and these endpoints return 409.
    if incident.approval_status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Incident {incident_id} has no remediation awaiting approval "
                   f"(approval_status={incident.approval_status})",
        )
    incident = store.decide_approval(incident_id, approved, body.decided_by, body.comment)
    log.info("approval decided", extra={"fields": {
        "incident_id": incident_id, "approved": approved, "decided_by": body.decided_by,
    }})
    return incident


@router.post("/{incident_id}/approve", response_model=Incident)
async def approve(incident_id: str, body: ApprovalRequest, store: IncidentStore = Depends(get_store)) -> Incident:
    return await _decide(incident_id, True, body, store)


@router.post("/{incident_id}/reject", response_model=Incident)
async def reject(incident_id: str, body: ApprovalRequest, store: IncidentStore = Depends(get_store)) -> Incident:
    return await _decide(incident_id, False, body, store)
