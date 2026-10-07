"""Collects all Kubernetes evidence for one incident by running the tools in order.

Convention: a service's Kubernetes objects carry the label
app.kubernetes.io/name=<service>, and its Deployment is named <service>.
That's the same value as the alert's `service` label.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from kubernetes.client.exceptions import ApiException

from app.logging_config import LOGGER_NAME
from app.models.evidence import KubernetesEvidence
from app.tools.kubernetes import KubernetesTools

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
