"""Pydantic models for the Alertmanager webhook payload (format version 4).

Reference: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
Field names match Alertmanager's JSON exactly (camelCase), so no aliases are needed.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AlertmanagerAlert(BaseModel):
    """One alert inside a webhook notification."""

    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    # Firing alerts carry the zero time "0001-01-01T00:00:00Z" here.
    endsAt: datetime | None = None
    generatorURL: str = ""
    # Stable hash of the alert's labels: the same problem always has the same
    # fingerprint, which is how repeated notifications are matched to one incident.
    fingerprint: str


class AlertmanagerWebhook(BaseModel):
    """A webhook notification. Alertmanager groups related alerts into one call."""

    version: str
    groupKey: str
    truncatedAlerts: int = 0
    status: Literal["firing", "resolved"]
    receiver: str
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""
    alerts: list[AlertmanagerAlert]
