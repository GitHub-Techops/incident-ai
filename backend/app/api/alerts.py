"""Alertmanager webhook: the entry point for every incident."""

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_store
from app.logging_config import LOGGER_NAME
from app.models.alertmanager import AlertmanagerWebhook
from app.models.incident import WebhookResult
from app.services.incident_store import IncidentStore

router = APIRouter(tags=["alerts"])
log = logging.getLogger(LOGGER_NAME)


@router.post("/webhook/alert", response_model=WebhookResult)
async def receive_alert(
    payload: AlertmanagerWebhook, store: IncidentStore = Depends(get_store)
) -> WebhookResult:
    result = WebhookResult(received=len(payload.alerts))

    for alert in payload.alerts:
        action, incident = store.record_alert(alert)
        log.info(f"alert {action}", extra={"fields": {
            "action": action,
            "incident_id": incident.incident_id if incident else None,
            "alertname": alert.labels.get("alertname"),
            "alert_status": alert.status,
            # Not "service"/"namespace": those would overwrite the log line's own
            # "service": "incident-backend" field (see logging_config).
            "affected_service": alert.labels.get("service"),
            "affected_namespace": alert.labels.get("namespace"),
            "severity": alert.labels.get("severity"),
            "fingerprint": alert.fingerprint,
        }})
        if incident is None:
            result.ignored += 1
        else:
            getattr(result, action).append(incident.incident_id)

    return result
