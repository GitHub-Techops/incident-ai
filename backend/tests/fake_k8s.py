"""A tiny in-memory stand-in for the Kubernetes API, built from the official
client's model classes, so tool tests run without a cluster."""

from datetime import UTC, datetime
from types import SimpleNamespace

from kubernetes import client as k
from kubernetes.client.exceptions import ApiException

from app.tools.kubernetes import KubernetesTools

NS = "incident-lab"
APP = "incident-demo"
LABELS = {"app.kubernetes.io/name": APP}
T0 = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 7, 8, 30, tzinfo=UTC)
DEPLOYMENT_UID = "deploy-uid"


def pod(name: str, ready: bool = True, restarts: int = 0, waiting: str | None = None,
        last_terminated: str | None = None) -> k.V1Pod:
    state = (k.V1ContainerState(waiting=k.V1ContainerStateWaiting(reason=waiting)) if waiting
             else k.V1ContainerState(running=k.V1ContainerStateRunning(started_at=T0)))
    last_state = (k.V1ContainerState(terminated=k.V1ContainerStateTerminated(exit_code=137, reason=last_terminated))
                  if last_terminated else None)
    return k.V1Pod(
        metadata=k.V1ObjectMeta(
            name=name, namespace=NS, labels=LABELS,
            owner_references=[k.V1OwnerReference(api_version="apps/v1", kind="ReplicaSet",
                                                 name=f"{APP}-rs2", uid="rs2")],
        ),
        spec=k.V1PodSpec(node_name="node-1", containers=[k.V1Container(name=APP, image=f"{APP}:v1")]),
        status=k.V1PodStatus(
            phase="Running", pod_ip="10.244.0.9", start_time=T0,
            conditions=[k.V1PodCondition(type="Ready", status="True" if ready else "False")],
            container_statuses=[k.V1ContainerStatus(
                name=APP, image=f"{APP}:v1", image_id="sha256:abc", ready=ready,
                restart_count=restarts, state=state, last_state=last_state,
            )],
        ),
    )


def template(env: list[k.V1EnvVar], image: str = f"{APP}:v1",
             annotations: dict[str, str] | None = None) -> k.V1PodTemplateSpec:
    return k.V1PodTemplateSpec(
        metadata=k.V1ObjectMeta(labels=LABELS, annotations=annotations),
        spec=k.V1PodSpec(containers=[k.V1Container(
            name=APP, image=image, env=env,
            env_from=[k.V1EnvFromSource(config_map_ref=k.V1ConfigMapEnvSource(name=f"{APP}-config"))],
        )]),
    )


def deployment(env: list[k.V1EnvVar], revision: str = "2") -> k.V1Deployment:
    return k.V1Deployment(
        metadata=k.V1ObjectMeta(name=APP, namespace=NS, uid=DEPLOYMENT_UID, labels=LABELS,
                                annotations={"deployment.kubernetes.io/revision": revision},
                                creation_timestamp=T0),
        spec=k.V1DeploymentSpec(replicas=2, selector=k.V1LabelSelector(match_labels=LABELS),
                                template=template(env)),
        status=k.V1DeploymentStatus(
            ready_replicas=2, available_replicas=2, updated_replicas=2,
            conditions=[k.V1DeploymentCondition(type="Available", status="True",
                                                reason="MinimumReplicasAvailable", message="ok")],
        ),
    )


def replicaset(name: str, revision: str, env: list[k.V1EnvVar], replicas: int,
               owner_uid: str = DEPLOYMENT_UID, created: datetime = T0, image: str = f"{APP}:v1",
               annotations: dict[str, str] | None = None) -> k.V1ReplicaSet:
    """annotations = pod template annotations (e.g. the git commit from deploy-demo.sh)."""
    return k.V1ReplicaSet(
        metadata=k.V1ObjectMeta(
            name=name, namespace=NS, creation_timestamp=created,
            annotations={"deployment.kubernetes.io/revision": revision},
            owner_references=[k.V1OwnerReference(api_version="apps/v1", kind="Deployment",
                                                 name=APP, uid=owner_uid)],
        ),
        spec=k.V1ReplicaSetSpec(selector=k.V1LabelSelector(match_labels=LABELS),
                                template=template(env, image, annotations)),
        status=k.V1ReplicaSetStatus(replicas=replicas),
    )


def event(name: str, reason: str, message: str, when: datetime | None, kind: str = "Pod",
          type_: str = "Normal") -> k.CoreV1Event:
    return k.CoreV1Event(
        metadata=k.V1ObjectMeta(name=f"{name}.1", namespace=NS),
        involved_object=k.V1ObjectReference(kind=kind, name=name, namespace=NS),
        reason=reason, message=message, type=type_, count=1,
        first_timestamp=when, last_timestamp=when,
    )


def items(objects) -> SimpleNamespace:
    return SimpleNamespace(items=list(objects))


class FakeCore:
    def __init__(self, pods=(), logs="", events=(), services=(), config_maps=()):
        self.pods, self.logs, self.events = list(pods), logs, list(events)
        self.services, self.config_maps = list(services), list(config_maps)
        self.calls: list[str] = []

    def list_namespaced_pod(self, namespace, label_selector=None):
        self.calls.append("list_namespaced_pod")
        return items(self.pods)

    def read_namespaced_pod_log(self, name, namespace, container=None, tail_lines=None, previous=False):
        self.calls.append(f"read_namespaced_pod_log previous={previous}")
        return self.logs

    def list_namespaced_event(self, namespace, field_selector=None):
        self.calls.append("list_namespaced_event")
        return items(self.events)

    def list_namespaced_service(self, namespace, label_selector=None):
        return items(self.services)

    def list_namespaced_config_map(self, namespace, label_selector=None):
        return items(self.config_maps)

    def read_namespaced_config_map(self, name, namespace):
        return next(cm for cm in self.config_maps if cm.metadata.name == name)


class FakeApps:
    def __init__(self, deployment=None, replicasets=()):
        self.deployment, self.replicasets = deployment, list(replicasets)

    def read_namespaced_deployment(self, name, namespace):
        if self.deployment is None:
            raise ApiException(status=404, reason="Not Found")
        return self.deployment

    def list_namespaced_replica_set(self, namespace, label_selector=None):
        return items(self.replicasets)


class FakeDiscovery:
    def __init__(self, endpoint_slices=()):
        self.endpoint_slices = list(endpoint_slices)

    def list_namespaced_endpoint_slice(self, namespace, label_selector=None):
        return items(self.endpoint_slices)


def make_tools(core=None, apps=None, discovery=None) -> KubernetesTools:
    return KubernetesTools(core or FakeCore(), apps or FakeApps(), discovery or FakeDiscovery(),
                           allowed_namespaces=frozenset({NS}), log_tail_lines=100)
