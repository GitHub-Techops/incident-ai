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
    # Pod template annotations, e.g. kubectl.kubernetes.io/restartedAt.
    annotations: dict[str, str] = Field(default_factory=dict)
    # Recorded by scripts/deploy-demo.sh; None for revisions deployed another way.
    git_commit: str | None = None
    git_ref: str | None = None
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


class MetricPoint(BaseModel):
    t: datetime
    v: float | None  # None where Prometheus returned NaN/Inf (e.g. 0/0 with no traffic)


class SeriesSummary(BaseModel):
    """The numbers an SRE reads off a graph, precomputed for the LLM.

    baseline: from the window start until 2 minutes before the alert (an alert
    only fires after its condition held for a while, so those minutes are
    already abnormal). incident: from the alert until it resolved (or until
    now, if still open). recovery: after the alert resolved.
    """

    baseline_avg: float | None = None
    incident_avg: float | None = None
    recovery_avg: float | None = None
    peak: float | None = None
    peak_at: datetime | None = None
    last: float | None = None


class MetricSeries(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict)  # e.g. {"pod": "..."} or {"status": "500"}
    summary: SeriesSummary = Field(default_factory=SeriesSummary)
    points: list[MetricPoint] = Field(default_factory=list)


class MetricResult(BaseModel):
    name: str  # e.g. "error_rate"
    description: str
    unit: str  # ratio | requests/s | seconds | cores | bytes
    query: str  # the exact PromQL, so every number is traceable and re-runnable
    series: list[MetricSeries] = Field(default_factory=list)


class MetricsEvidence(BaseModel):
    namespace: str
    service: str
    collected_at: datetime
    incident_time: datetime  # when the alert started firing
    resolved_time: datetime | None = None  # when the alert stopped firing, if it has
    window_start: datetime
    window_end: datetime
    step_seconds: int
    metrics: list[MetricResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FileChange(BaseModel):
    path: str
    status: str  # A added | M modified | D deleted | R renamed ...
    additions: int | None = None  # None for binary files
    deletions: int | None = None


class CommitInfo(BaseModel):
    sha: str
    short_sha: str
    author: str  # name only; emails stay out of LLM prompts
    authored_at: datetime
    committed_at: datetime
    subject: str
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    files: list[FileChange] = Field(default_factory=list)  # limited to the service's path


class GitDiff(BaseModel):
    from_sha: str
    to_sha: str
    path: str  # only changes under this path, e.g. "demo-app"
    files: list[FileChange] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    patch: str  # unified diff, possibly truncated
    truncated: bool = False


class DeploymentEvidence(BaseModel):
    """What changed before the incident: Kubernetes revisions joined with Git history.

    The "active" revision is the newest one created at or before the alert;
    "previous" is the one before it. The commits and diff are the code that
    changed between them. Revisions created after the alert (a fix, a
    rollback) are listed separately.
    """

    namespace: str
    service: str
    collected_at: datetime
    incident_time: datetime
    source_path: str  # where the service's code lives in the repo
    active_revision: DeploymentRevision | None = None
    previous_revision: DeploymentRevision | None = None
    deployed_before_incident_seconds: float | None = None
    revisions_after_incident: list[DeploymentRevision] = Field(default_factory=list)
    current_commit: CommitInfo | None = None
    previous_commit: CommitInfo | None = None
    commits: list[CommitInfo] = Field(default_factory=list)  # previous..active, newest first
    diff: GitDiff | None = None
    # Plain-language facts derived only from the fields above, for the LLM and humans.
    facts: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


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
