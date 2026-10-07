"""Read-only Kubernetes investigation tools.

Each method answers one question an SRE would ask during an incident and
returns Pydantic models (structured evidence), never raw API objects or
kubectl text.

Safety is enforced twice:
  1. Here: a namespace outside the allow-list is rejected before any API call.
  2. In the cluster: the backend's ServiceAccount only has get/list RBAC in
     incident-lab (k8s/backend/rbac.yaml), so even a bug here cannot write
     anything or read other namespaces.

Uses the official Kubernetes Python client. Nothing shells out to kubectl.
"""

import json
import re
from datetime import UTC, datetime

from kubernetes import client, config

from app.models.evidence import (
    ConfigMapInfo,
    ContainerInfo,
    DeploymentInfo,
    DeploymentRevision,
    EventInfo,
    PodInfo,
    PodLogs,
    ServiceInfo,
)

# Env var / ConfigMap keys whose values are never passed on (they will end up
# in LLM prompts). Values sourced from Secrets are never read at all.
SECRET_LIKE_KEY = re.compile(r"pass|secret|token|key|credential", re.IGNORECASE)
# For plain-text (non-JSON) log lines.
ERROR_HINT = re.compile(r"error|exception|traceback|fatal|panic|refused|timed? ?out", re.IGNORECASE)
ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}

MAX_LOG_CHARS = 20_000
MAX_ERROR_LINES = 20
MAX_CONFIG_VALUE_CHARS = 2_000
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # sort key for events without timestamps


class NamespaceNotAllowedError(PermissionError):
    pass


# ---------------------------------------------------------------- helpers ---

def is_error_line(line: str) -> bool:
    """JSON logs: trust the level field. Plain text: look for error words."""
    try:
        entry = json.loads(line)
    except ValueError:
        entry = None
    if isinstance(entry, dict):
        return str(entry.get("level", "")).upper() in ERROR_LEVELS
    return bool(ERROR_HINT.search(line))


def _render_env(env_vars: list[client.V1EnvVar] | None) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for var in env_vars or []:
        source = var.value_from
        if source is not None:
            if source.secret_key_ref is not None:
                ref = source.secret_key_ref
                rendered[var.name] = f"<from secret {ref.name}/{ref.key}>"
            elif source.config_map_key_ref is not None:
                ref = source.config_map_key_ref
                rendered[var.name] = f"<from configmap {ref.name}/{ref.key}>"
            else:
                rendered[var.name] = "<from field or resource reference>"
        elif SECRET_LIKE_KEY.search(var.name):
            rendered[var.name] = "<redacted>"
        else:
            rendered[var.name] = var.value or ""
    return rendered


def _template_env(template: client.V1PodTemplateSpec) -> dict[str, str]:
    containers = template.spec.containers
    if len(containers) == 1:
        return _render_env(containers[0].env)
    # Several containers: prefix each variable with its container name.
    return {
        f"{c.name}/{name}": value
        for c in containers
        for name, value in _render_env(c.env).items()
    }


def _template_env_from(template: client.V1PodTemplateSpec) -> list[str]:
    sources: list[str] = []
    for c in template.spec.containers:
        for source in c.env_from or []:
            if source.config_map_ref is not None:
                sources.append(f"configmap/{source.config_map_ref.name}")
            elif source.secret_ref is not None:
                sources.append(f"secret/{source.secret_ref.name}")
    return sources


def _template_images(template: client.V1PodTemplateSpec) -> list[str]:
    return [c.image for c in template.spec.containers]


def _diff_revisions(previous: DeploymentRevision, current: DeploymentRevision) -> list[str]:
    changes: list[str] = []
    if previous.images != current.images:
        changes.append(f"image: {', '.join(previous.images)} -> {', '.join(current.images)}")
    for name in sorted(set(previous.env) | set(current.env)):
        before, after = previous.env.get(name), current.env.get(name)
        if before != after:
            changes.append(f"env {name}: {before or '<unset>'} -> {after or '<unset>'}")
    return changes


def _container_info(status: client.V1ContainerStatus) -> ContainerInfo:
    state, reason = "unknown", None
    if status.state is not None:
        if status.state.running is not None:
            state = "running"
        elif status.state.waiting is not None:
            state, reason = "waiting", status.state.waiting.reason
        elif status.state.terminated is not None:
            state, reason = "terminated", status.state.terminated.reason
    last = status.last_state.terminated if status.last_state is not None else None
    return ContainerInfo(
        name=status.name,
        image=status.image,
        ready=bool(status.ready),
        restart_count=status.restart_count or 0,
        state=state,
        reason=reason,
        last_termination_reason=last.reason if last is not None else None,
    )


def _event_info(event: client.CoreV1Event) -> EventInfo:
    created = event.metadata.creation_timestamp if event.metadata is not None else None
    involved = event.involved_object
    return EventInfo(
        type=event.type,
        reason=event.reason,
        message=event.message,
        object_kind=involved.kind if involved is not None else None,
        object_name=involved.name if involved is not None else None,
        count=event.count or 1,
        first_seen=event.first_timestamp or event.event_time or created,
        last_seen=event.last_timestamp or event.event_time or created,
    )


def _event_sort_key(event: EventInfo) -> datetime:
    return event.last_seen or event.first_seen or EPOCH


def _selector_string(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


# ------------------------------------------------------------------ tools ---

class KubernetesTools:
    def __init__(
        self,
        core: client.CoreV1Api,
        apps: client.AppsV1Api,
        discovery: client.DiscoveryV1Api,
        allowed_namespaces: frozenset[str],
        log_tail_lines: int = 100,
    ) -> None:
        self.core = core
        self.apps = apps
        self.discovery = discovery
        self.allowed_namespaces = allowed_namespaces
        self.log_tail_lines = log_tail_lines

    @classmethod
    def from_environment(cls, allowed_namespaces: frozenset[str], log_tail_lines: int = 100) -> "KubernetesTools":
        """Inside a pod: use the ServiceAccount token. Locally: use ~/.kube/config."""
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return cls(client.CoreV1Api(), client.AppsV1Api(), client.DiscoveryV1Api(),
                   allowed_namespaces, log_tail_lines)

    def check_namespace(self, namespace: str) -> None:
        if namespace not in self.allowed_namespaces:
            raise NamespaceNotAllowedError(
                f"Namespace '{namespace}' is not allowed; allowed: {sorted(self.allowed_namespaces)}"
            )

    def get_pods(self, namespace: str, label_selector: str | None = None) -> list[PodInfo]:
        self.check_namespace(namespace)
        pods = self.core.list_namespaced_pod(namespace=namespace, label_selector=label_selector).items
        result = []
        for pod in pods:
            statuses = pod.status.container_statuses or []
            conditions = pod.status.conditions or []
            owners = pod.metadata.owner_references or []
            result.append(PodInfo(
                name=pod.metadata.name,
                phase=pod.status.phase,
                ready=any(c.type == "Ready" and c.status == "True" for c in conditions),
                restarts=sum(s.restart_count or 0 for s in statuses),
                node=pod.spec.node_name,
                pod_ip=pod.status.pod_ip,
                started_at=pod.status.start_time,
                owner=owners[0].name if owners else None,
                labels=pod.metadata.labels or {},
                containers=[_container_info(s) for s in statuses],
            ))
        return sorted(result, key=lambda p: p.name)

    def get_pod_logs(
        self,
        namespace: str,
        pod: str,
        container: str | None = None,
        tail_lines: int | None = None,
        previous: bool = False,
    ) -> PodLogs:
        self.check_namespace(namespace)
        text = self.core.read_namespaced_pod_log(
            name=pod,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines or self.log_tail_lines,
            previous=previous,
        ) or ""
        truncated = len(text) > MAX_LOG_CHARS
        if truncated:
            text = text[-MAX_LOG_CHARS:]  # keep the most recent part
        lines = text.splitlines()
        errors = [line for line in lines if is_error_line(line)]
        return PodLogs(
            pod=pod,
            container=container,
            previous=previous,
            line_count=len(lines),
            error_line_count=len(errors),
            error_lines=errors[-MAX_ERROR_LINES:],
            logs=text,
            truncated=truncated,
        )

    def get_pod_events(self, namespace: str, pod: str) -> list[EventInfo]:
        self.check_namespace(namespace)
        events = self.core.list_namespaced_event(
            namespace=namespace, field_selector=f"involvedObject.name={pod}"
        ).items
        return sorted((_event_info(e) for e in events), key=_event_sort_key, reverse=True)

    def get_events(self, namespace: str, name_prefix: str | None = None, limit: int = 50) -> list[EventInfo]:
        """Recent namespace events, newest first, optionally only for objects whose
        name starts with name_prefix (the deployment, its ReplicaSets and pods)."""
        self.check_namespace(namespace)
        events = [_event_info(e) for e in self.core.list_namespaced_event(namespace=namespace).items]
        if name_prefix:
            events = [e for e in events if (e.object_name or "").startswith(name_prefix)]
        events.sort(key=_event_sort_key, reverse=True)
        return events[:limit]

    def get_deployment(self, namespace: str, name: str) -> DeploymentInfo:
        self.check_namespace(namespace)
        d = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
        status = d.status
        return DeploymentInfo(
            name=d.metadata.name,
            namespace=d.metadata.namespace,
            revision=(d.metadata.annotations or {}).get(REVISION_ANNOTATION),
            desired_replicas=d.spec.replicas or 0,
            ready_replicas=status.ready_replicas or 0,
            available_replicas=status.available_replicas or 0,
            updated_replicas=status.updated_replicas or 0,
            unavailable_replicas=status.unavailable_replicas or 0,
            images=_template_images(d.spec.template),
            env=_template_env(d.spec.template),
            env_from=_template_env_from(d.spec.template),
            conditions=[
                f"{c.type}={c.status}" + (f" ({c.reason}: {c.message})" if c.reason else "")
                for c in status.conditions or []
            ],
            labels=d.metadata.labels or {},
            created_at=d.metadata.creation_timestamp,
        )

    def get_deployment_history(self, namespace: str, name: str) -> list[DeploymentRevision]:
        """Like `kubectl rollout history`, plus what changed in each revision. Newest first."""
        self.check_namespace(namespace)
        d = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
        current_revision = (d.metadata.annotations or {}).get(REVISION_ANNOTATION)
        replicasets = self.apps.list_namespaced_replica_set(
            namespace=namespace, label_selector=_selector_string(d.spec.selector.match_labels or {})
        ).items
        # Only ReplicaSets owned by this deployment.
        owned = [rs for rs in replicasets
                 if any(o.uid == d.metadata.uid for o in rs.metadata.owner_references or [])]

        revisions = []
        for rs in owned:
            annotations = rs.metadata.annotations or {}
            revision = annotations.get(REVISION_ANNOTATION)
            if revision is None:
                continue
            revisions.append(DeploymentRevision(
                revision=int(revision),
                replicaset=rs.metadata.name,
                current=revision == current_revision,
                replicas=rs.status.replicas or 0,
                created_at=rs.metadata.creation_timestamp,
                images=_template_images(rs.spec.template),
                env=_template_env(rs.spec.template),
                change_cause=annotations.get(CHANGE_CAUSE_ANNOTATION),
            ))

        revisions.sort(key=lambda r: r.revision)
        for previous, current in zip(revisions, revisions[1:]):
            current.changes_from_previous = _diff_revisions(previous, current)
        return list(reversed(revisions))

    def get_services(self, namespace: str, label_selector: str | None = None) -> list[ServiceInfo]:
        self.check_namespace(namespace)
        services = self.core.list_namespaced_service(namespace=namespace, label_selector=label_selector).items
        result = []
        for svc in services:
            slices = self.discovery.list_namespaced_endpoint_slice(
                namespace=namespace, label_selector=f"kubernetes.io/service-name={svc.metadata.name}"
            ).items
            ready = not_ready = 0
            for endpoint_slice in slices:
                for endpoint in endpoint_slice.endpoints or []:
                    # Per the API, an unset "ready" condition means ready.
                    conditions = endpoint.conditions
                    if conditions is None or conditions.ready is not False:
                        ready += 1
                    else:
                        not_ready += 1
            result.append(ServiceInfo(
                name=svc.metadata.name,
                type=svc.spec.type,
                cluster_ip=svc.spec.cluster_ip,
                ports=[f"{p.name or ''}:{p.port}->{p.target_port}/{p.protocol}" for p in svc.spec.ports or []],
                selector=svc.spec.selector or {},
                ready_endpoints=ready,
                not_ready_endpoints=not_ready,
            ))
        return result

    def get_config(
        self, namespace: str, name: str | None = None, label_selector: str | None = None
    ) -> list[ConfigMapInfo]:
        """ConfigMaps (never Secrets). Secret-looking keys are redacted."""
        self.check_namespace(namespace)
        if name is not None:
            config_maps = [self.core.read_namespaced_config_map(name=name, namespace=namespace)]
        else:
            config_maps = self.core.list_namespaced_config_map(
                namespace=namespace, label_selector=label_selector
            ).items
        result = []
        for cm in config_maps:
            if cm.metadata.name == "kube-root-ca.crt":  # cluster CA, present in every namespace
                continue
            data = {
                key: "<redacted>" if SECRET_LIKE_KEY.search(key) else value[:MAX_CONFIG_VALUE_CHARS]
                for key, value in (cm.data or {}).items()
            }
            result.append(ConfigMapInfo(name=cm.metadata.name, data=data))
        return result
