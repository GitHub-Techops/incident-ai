"""Incident read and approval endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_store
from app.logging_config import LOGGER_NAME
from app.models.incident import ApprovalRequest, ApprovalStatus, Incident, IncidentStatus
from app.services.incident_store import IncidentStore

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
