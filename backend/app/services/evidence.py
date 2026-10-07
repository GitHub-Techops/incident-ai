"""Collects evidence for one incident by running the investigation tools.

Convention: a service's Kubernetes objects carry the label
app.kubernetes.io/name=<service>, and its Deployment is named <service>.
That's the same value as the alert's `service` label.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from kubernetes.client.exceptions import ApiException

from app.logging_config import LOGGER_NAME
from app.models.evidence import DeploymentEvidence, DeploymentRevision, KubernetesEvidence, MetricsEvidence
from app.tools.errors import InvalidTargetError
from app.tools.git import GitError, GitTools
from app.tools.kubernetes import KubernetesTools
from app.tools.prometheus import MAX_WINDOW, PrometheusError, PrometheusTools, choose_step

log = logging.getLogger(LOGGER_NAME)
T = TypeVar("T")

MAX_PODS_FOR_LOGS = 5


def collect_kubernetes_evidence(tools: KubernetesTools, namespace: str, service: str) -> KubernetesEvidence:
    # Raises NamespaceNotAllowedError before anything else: never partial-collect there.
    tools.check_namespace(namespace)

    evidence = KubernetesEvidence(namespace=namespace, service=service, collected_at=datetime.now(UTC))

    def attempt(what: str, call: Callable[[], T]) -> T | None:
        """Run one tool. On failure, record the gap and keep investigating."""
        try:
            return call()
        except ApiException as exc:
            evidence.errors.append(f"{what}: HTTP {exc.status} {exc.reason}")
        except Exception as exc:  # noqa: BLE001 - one failed tool must not lose the rest
            evidence.errors.append(f"{what}: {type(exc).__name__}: {exc}")
        return None

    selector = f"app.kubernetes.io/name={service}"

    evidence.pods = attempt("get_pods", lambda: tools.get_pods(namespace, selector)) or []

    for pod in evidence.pods[:MAX_PODS_FOR_LOGS]:
        container = pod.containers[0].name if pod.containers else None
        logs = attempt(f"get_pod_logs {pod.name}",
                       lambda: tools.get_pod_logs(namespace, pod.name, container))
        if logs:
            evidence.logs.append(logs)
        # A restarted container's crash output is in its *previous* logs.
        if pod.restarts > 0:
            previous = attempt(f"get_pod_logs {pod.name} (previous)",
                               lambda: tools.get_pod_logs(namespace, pod.name, container, previous=True))
            if previous:
                evidence.logs.append(previous)

    evidence.events = attempt("get_events", lambda: tools.get_events(namespace, name_prefix=service)) or []
    evidence.deployment = attempt("get_deployment", lambda: tools.get_deployment(namespace, service))
    evidence.deployment_history = attempt(
        "get_deployment_history", lambda: tools.get_deployment_history(namespace, service)) or []
    evidence.services = attempt("get_services", lambda: tools.get_services(namespace, selector)) or []
    evidence.configs = attempt("get_config", lambda: tools.get_config(namespace, label_selector=selector)) or []

    log.info("kubernetes evidence collected", extra={"fields": {
        "affected_namespace": namespace,
        "affected_service": service,
        "pods": len(evidence.pods),
        "error_log_lines": sum(l.error_line_count for l in evidence.logs),
        "events": len(evidence.events),
        "revisions": len(evidence.deployment_history),
        "tool_errors": len(evidence.errors),
    }})
    return evidence


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(abs(seconds)), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _describe(revision: DeploymentRevision) -> str:
    commit = f", commit {revision.git_commit[:7]}" if revision.git_commit else ""
    return f"revision {revision.revision} ({', '.join(revision.images)}{commit})"


def collect_deployment_evidence(
    k8s: KubernetesTools, git: GitTools, namespace: str, service: str, incident_time: datetime,
) -> DeploymentEvidence:
    """What changed immediately before the incident: the revision running when
    the alert fired, the one before it, and the code between their commits."""
    k8s.check_namespace(namespace)  # raises before any call
    evidence = DeploymentEvidence(
        namespace=namespace, service=service, collected_at=datetime.now(UTC),
        incident_time=incident_time, source_path=git.path_for(service),  # raises if unmapped
    )

    try:
        history = k8s.get_deployment_history(namespace, service)  # newest first
    except ApiException as exc:
        evidence.errors.append(f"get_deployment_history: HTTP {exc.status} {exc.reason}")
        history = []

    before = [r for r in history if r.created_at is not None and r.created_at <= incident_time]
    evidence.revisions_after_incident = [r for r in history if r not in before]
    if not before:
        if history:
            evidence.errors.append("no deployment revision was created before the incident")
        return _finish(evidence)

    active = evidence.active_revision = before[0]
    previous = evidence.previous_revision = before[1] if len(before) > 1 else None
    evidence.deployed_before_incident_seconds = (incident_time - active.created_at).total_seconds()

    fact = f"{_describe(active)} was deployed {_duration(evidence.deployed_before_incident_seconds)} before the alert"
    if previous is not None:
        fact += f", replacing {_describe(previous)}"
    evidence.facts.append(fact + ".")
    if active.changes_from_previous:
        evidence.facts.append(
            f"Kubernetes changes in revision {active.revision}: {'; '.join(active.changes_from_previous)}.")
    elif previous is not None:
        evidence.facts.append(f"Revision {active.revision} has no image, env or annotation change "
                              f"from revision {previous.revision}.")

    # ---- Git: what code is behind those revisions
    if active.git_commit is None:
        evidence.errors.append(f"revision {active.revision} has no git commit annotation "
                               "(not deployed with scripts/deploy-demo.sh)")
        return _finish(evidence)
    try:
        git.sync()
    except GitError as exc:
        evidence.errors.append(f"git sync: {exc} (using previously fetched history, if any)")

    try:
        evidence.current_commit = git.get_commit(service, active.git_commit)
        if previous is None or previous.git_commit is None:
            evidence.errors.append("previous revision has no git commit annotation; "
                                   "showing the latest commits to the service instead of a diff")
            evidence.commits = git.get_recent_commits(service, active.git_commit, limit=5)
        elif previous.git_commit == active.git_commit:
            evidence.previous_commit = evidence.current_commit
            evidence.facts.append(f"Same code as revision {previous.revision} (commit {active.git_commit[:7]}): "
                                  "this revision changed only the Kubernetes deployment, not the code.")
        else:
            evidence.previous_commit = git.get_commit(service, previous.git_commit)
            evidence.commits = git.get_commits_between(service, previous.git_commit, active.git_commit)
            evidence.diff = git.get_diff(service, previous.git_commit, active.git_commit)
            d = evidence.diff
            evidence.facts.append(
                f"Code change from {previous.git_commit[:7]} to {active.git_commit[:7]} in {d.path}/: "
                f"{len(evidence.commits)} commit(s), {len(d.files)} file(s) changed, +{d.additions} -{d.deletions}.")
            for c in evidence.commits:
                evidence.facts.append(
                    f"Commit {c.short_sha} by {c.author} at {c.committed_at.isoformat()}: {c.subject} "
                    f"(files: {', '.join(f.path for f in c.files) or 'none under ' + d.path})")
    except (GitError, InvalidTargetError) as exc:
        evidence.errors.append(f"git: {exc}")

    return _finish(evidence)


def _finish(evidence: DeploymentEvidence) -> DeploymentEvidence:
    for r in reversed(evidence.revisions_after_incident):  # oldest first
        delay = (r.created_at - evidence.incident_time).total_seconds() if r.created_at else None
        when = f"{_duration(delay)} after the alert" if delay is not None else "after the alert"
        changes = f": {'; '.join(r.changes_from_previous)}" if r.changes_from_previous else ""
        evidence.facts.append(f"{_describe(r)} was deployed {when}{changes}.")
    log.info("deployment evidence collected", extra={"fields": {
        "affected_namespace": evidence.namespace,
        "affected_service": evidence.service,
        "active_revision": evidence.active_revision.revision if evidence.active_revision else None,
        "commits": len(evidence.commits),
        "tool_errors": len(evidence.errors),
    }})
    return evidence


def collect_metrics_evidence(
    tools: PrometheusTools,
    namespace: str,
    service: str,
    incident_time: datetime,
    lookback: timedelta,
    end: datetime | None = None,
    resolved_time: datetime | None = None,
) -> MetricsEvidence:
    """Metrics from `lookback` before the alert until `end` (default: now),
    summarized per phase: baseline, incident, and (if resolved) recovery."""
    tools.check_target(namespace, service)  # raises before any query

    start = incident_time - lookback
    # Long-running incidents: look at the first MAX_WINDOW only.
    end = min(end or datetime.now(UTC), start + MAX_WINDOW)
    evidence = MetricsEvidence(
        namespace=namespace, service=service, collected_at=datetime.now(UTC),
        incident_time=incident_time, resolved_time=resolved_time, window_start=start, window_end=end,
        step_seconds=choose_step(start, end),
    )
    queries = {
        "request_rate": tools.get_request_rate,
        "error_rate": tools.get_error_rate,
        "latency_p95": tools.get_latency,
        "cpu_usage": tools.get_cpu_usage,
        "memory_usage": tools.get_memory_usage,
    }
    for name, get_metric in queries.items():
        try:
            evidence.metrics.append(get_metric(namespace, service, start, end, incident_time, resolved_time))
        except PrometheusError as exc:
            evidence.errors.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one failed query must not lose the rest
            evidence.errors.append(f"{name}: {type(exc).__name__}: {exc}")

    log.info("metrics evidence collected", extra={"fields": {
        "affected_namespace": namespace,
        "affected_service": service,
        "window_minutes": round((end - start).total_seconds() / 60, 1),
        "metrics": len(evidence.metrics),
        "tool_errors": len(evidence.errors),
    }})
    return evidence
