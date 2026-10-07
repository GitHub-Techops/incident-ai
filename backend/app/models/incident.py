"""Incident models: what the backend stores and returns."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IncidentStatus(StrEnum):
    OPEN = "open"
    # The alert stopped firing. Later milestones add investigation and
    # remediation states between OPEN and RESOLVED.
    RESOLVED = "resolved"


class ApprovalStatus(StrEnum):
    # No remediation has been recommended yet, so there is nothing to approve.
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TimelineEvent(BaseModel):
    at: datetime
    event: str


class Incident(BaseModel):
    incident_id: str
    fingerprint: str
    alert_name: str
    service: str | None = None
    namespace: str | None = None
    severity: str | None = None
    summary: str | None = None
    description: str | None = None
    status: IncidentStatus = IncidentStatus.OPEN
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    # Latest alert exactly as Alertmanager sent it: the raw evidence.
    alert: dict[str, Any]
    timeline: list[TimelineEvent] = Field(default_factory=list)


class WebhookResult(BaseModel):
    """What the webhook did with each alert in a notification."""

    received: int
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    resolved: list[str] = Field(default_factory=list)
    ignored: int = 0


class ApprovalRequest(BaseModel):
    decided_by: str = Field(min_length=1)
    comment: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    incident_id: str | None = None
