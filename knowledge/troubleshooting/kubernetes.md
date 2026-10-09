---
title: Investigating a service in Kubernetes
type: troubleshooting
services: [any]
alerts: []
tags: [kubernetes, kubectl, pods, events, logs, read-only]
---

# Investigating a service in Kubernetes

All commands here are read-only. Services are labelled
`app.kubernetes.io/name=<service>`, and their Deployment has the same name.

## Pods: running, ready, restarting?

```bash
kubectl get pods -n <namespace> -l app.kubernetes.io/name=<service> -o wide
```

- `STATUS Running` + `READY 1/1`: the container runs and passes its readiness
  probe. It does not prove that requests succeed.
- `RESTARTS` rising: the container crashes; see the CrashLoopBackOff runbook.
- `Pending`: not scheduled (resources, node selector, volume).
- `ImagePullBackOff` / `ErrImageNeverPull`: the image is missing. In kind,
  images must be loaded with `kind load docker-image`.

The pod name's middle part is the ReplicaSet hash: pods with different hashes
belong to different revisions, which shows a rollout in progress.

## Events

```bash
kubectl get events -n <namespace> --sort-by=.lastTimestamp
kubectl describe pod <pod> -n <namespace>
```

Events show scheduling, image pulls, probe failures, kills and scaling. They
expire after about an hour, so read them early in an incident.

## Logs

```bash
kubectl logs deployment/<service> -n <namespace> --tail=100
kubectl logs <pod> -n <namespace> --previous    # the crashed container
```

The demo services log one JSON object per line with `level`, `msg` and
context fields such as `path`, `status`, `error`, `db_host`. Filter error lines
by level rather than by searching for words.

## Deployment, configuration and history

```bash
kubectl get deployment <service> -n <namespace> -o yaml
kubectl rollout history deployment/<service> -n <namespace>
kubectl get configmap -n <namespace> -l app.kubernetes.io/name=<service> -o yaml
```

An environment variable set directly on the deployment (for example with
`kubectl set env`) overrides the same key loaded from a ConfigMap with
`envFrom`. When the ConfigMap and the deployment disagree, the deployment wins.

## Services and endpoints

```bash
kubectl get svc,endpointslices -n <namespace> -l app.kubernetes.io/name=<service>
```

A Service with zero ready endpoints sends no traffic anywhere: all its pods
are failing readiness.

## Using the incident backend instead

The incident backend collects the same evidence through the Kubernetes API with
read-only permissions:

```bash
curl -s -X POST localhost:8080/incidents/<id>/evidence/kubernetes
curl -s -X POST localhost:8080/incidents/<id>/evidence/deployment
```

It returns pods, error log lines, events, deployment and rollout history with
the changes in each revision, services and ConfigMaps (secret-like values
redacted), plus the Git commits and diff behind the latest revisions.
