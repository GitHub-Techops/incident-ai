"""In-memory incident store.

Temporary: incidents are lost when the pod restarts. PostgreSQL replaces this
in a later milestone behind the same methods, so the API layer won't change.

All API handlers are async and run on a single event loop, so these methods
are never called concurrently and need no locking.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from app.models.alertmanager import AlertmanagerAlert
from app.models.incident import (
    ApprovalStatus,
    Incident,
    IncidentStatus,
    TimelineEvent,
)

AlertAction = Literal["created", "updated", "resolved", "ignored"]


def _now() -> datetime:
    return datetime.now(UTC)


def _new_incident_id(now: datetime) -> str:
    return f"INC-{now:%Y%m%d}-{uuid.uuid4().hex[:6]}"


class IncidentStore:
    def __init__(self) -> None:
        self._incidents: dict[str, Incident] = {}
        # fingerprint -> incident_id, only while the incident is open
        self._open_by_fingerprint: dict[str, str] = {}

    def record_alert(self, alert: AlertmanagerAlert) -> tuple[AlertAction, Incident | None]:
        """Create, update or resolve the incident that matches this alert."""
        now = _now()
        open_id = self._open_by_fingerprint.get(alert.fingerprint)
        raw_alert = alert.model_dump(mode="json")

        if alert.status == "firing":
            if open_id is not None:
                # Alertmanager re-sends active alerts periodically: same incident.
                incident = self._incidents[open_id]
                incident.alert = raw_alert
                incident.updated_at = now
                return "updated", incident

            incident = Incident(
                incident_id=_new_incident_id(now),
                fingerprint=alert.fingerprint,
                alert_name=alert.labels.get("alertname", "unknown"),
                service=alert.labels.get("service"),
                namespace=alert.labels.get("namespace"),
                severity=alert.labels.get("severity"),
                summary=alert.annotations.get("summary"),
                description=alert.annotations.get("description"),
                created_at=now,
                updated_at=now,
                alert=raw_alert,
                timeline=[
                    TimelineEvent(at=alert.startsAt, event="Alert started firing"),
                    TimelineEvent(at=now, event="Incident created from Alertmanager webhook"),
                ],
            )
            self._incidents[incident.incident_id] = incident
            self._open_by_fingerprint[alert.fingerprint] = incident.incident_id
            return "created", incident

        # status == "resolved"
        if open_id is None:
            # Resolved notice for an incident we never saw (e.g. backend restarted).
            return "ignored", None
        incident = self._incidents[open_id]
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = alert.endsAt or now
        incident.updated_at = now
        incident.alert = raw_alert
        incident.timeline.append(TimelineEvent(at=now, event="Alert resolved"))
        del self._open_by_fingerprint[alert.fingerprint]
        return "resolved", incident

    def list_incidents(self, status: IncidentStatus | None = None) -> list[Incident]:
        incidents = [i for i in self._incidents.values() if status is None or i.status == status]
        return sorted(incidents, key=lambda i: i.created_at, reverse=True)

    def get(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    def decide_approval(
        self, incident_id: str, approved: bool, decided_by: str, comment: str | None
    ) -> Incident:
        """Record a human approval decision. Caller must check approval is pending."""
        incident = self._incidents[incident_id]
        incident.approval_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        incident.updated_at = _now()
        verb = "approved" if approved else "rejected"
        note = f": {comment}" if comment else ""
        incident.timeline.append(
            TimelineEvent(at=incident.updated_at, event=f"Remediation {verb} by {decided_by}{note}")
        )
        return incident
