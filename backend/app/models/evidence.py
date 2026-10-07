"""Structured Kubernetes evidence returned by the investigation tools.

These are facts read from the cluster, with no interpretation. Later the LLM
reasons over them, and every claim it makes can be traced back to a field here.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class ContainerInfo(BaseModel):
    name: str
    image: str
    ready: bool
    restart_count: int
    state: str  # running | waiting | terminated | unknown
    reason: str | None = None  # e.g. CrashLoopBackOff, OOMKilled
    last_termination_reason: str | None = None


class PodInfo(BaseModel):
    name: str
    phase: str | None
    ready: bool
    restarts: int
    node: str | None = None
    pod_ip: str | None = None
    started_at: datetime | None = None
    owner: str | None = None  # usually the ReplicaSet, i.e. which deployment revision
    labels: dict[str, str] = Field(default_factory=dict)
    containers: list[ContainerInfo] = Field(default_factory=list)


class PodLogs(BaseModel):
    pod: str
    container: str | None
    previous: bool = False  # True = logs of the crashed (previous) container
    line_count: int
    error_line_count: int
    error_lines: list[str] = Field(default_factory=list)  # most recent error lines
    logs: str  # tail of the log
    truncated: bool = False


class EventInfo(BaseModel):
    type: str | None  # Normal | Warning
    reason: str | None
    message: str | None
    object_kind: str | None
    object_name: str | None
    count: int = 1
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class DeploymentInfo(BaseModel):
    name: str
    namespace: str
    revision: str | None
    desired_replicas: int
    ready_replicas: int
    available_replicas: int
    updated_replicas: int
    unavailable_replicas: int
    images: list[str]
    # Env vars set directly on the pod template. Secret-looking values are redacted;
    # values from Secrets/ConfigMaps are shown as references, not read.
    env: dict[str, str] = Field(default_factory=dict)
    env_from: list[str] = Field(default_factory=list)  # e.g. ["configmap/incident-demo-config"]
    conditions: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None


class DeploymentRevision(BaseModel):
    """One entry of `kubectl rollout history`, with what changed versus the one before.

    created_at is when the ReplicaSet was first created. A rollback reuses an old
    ReplicaSet (and gives it a new revision number), so it keeps its old date.
    """

    revision: int
    replicaset: str
    current: bool
    replicas: int
    created_at: datetime | None = None
    images: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    change_cause: str | None = None
    # Note: changes to a referenced ConfigMap's *contents* don't create a revision,
    # so they don't show up here.
    changes_from_previous: list[str] = Field(default_factory=list)


class ServiceInfo(BaseModel):
    name: str
    type: str | None
    cluster_ip: str | None
    ports: list[str] = Field(default_factory=list)
    selector: dict[str, str] = Field(default_factory=dict)
    ready_endpoints: int
    not_ready_endpoints: int


class ConfigMapInfo(BaseModel):
    name: str
    data: dict[str, str] = Field(default_factory=dict)


class KubernetesEvidence(BaseModel):
    namespace: str
    service: str
    collected_at: datetime
    pods: list[PodInfo] = Field(default_factory=list)
    logs: list[PodLogs] = Field(default_factory=list)
    events: list[EventInfo] = Field(default_factory=list)
    deployment: DeploymentInfo | None = None
    deployment_history: list[DeploymentRevision] = Field(default_factory=list)
    services: list[ServiceInfo] = Field(default_factory=list)
    configs: list[ConfigMapInfo] = Field(default_factory=list)
    # Tool calls that failed (e.g. not found, forbidden). Partial evidence is
    # still returned, and the gaps are explicit instead of silently missing.
    errors: list[str] = Field(default_factory=list)
