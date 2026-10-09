---
title: CrashLoopBackOff
type: runbook
services: [any]
alerts: []
tags: [kubernetes, crashloopbackoff, restarts, oomkilled, probes]
---

# CrashLoopBackOff

## What it means

A container keeps exiting and Kubernetes restarts it with growing delays
(10s, 20s, 40s ... up to 5 minutes). The pod shows `CrashLoopBackOff` in its
status and a rising restart count. The application starts, fails, and exits.

## Find out why it exits

```bash
kubectl get pods -n <namespace>
kubectl describe pod <pod> -n <namespace>
kubectl logs <pod> -n <namespace> --previous
```

`--previous` shows the logs of the container that crashed, which is where the
error is. The current container may have just started and logged nothing yet.

In `describe`, look at "Last State: Terminated" and its reason and exit code.

## Common reasons

| Last state reason / exit code | Usual cause |
|---|---|
| `OOMKilled`, exit 137 | Memory limit too low or a memory leak |
| `Error`, exit 1 | The application raised an error at startup |
| `Completed`, exit 0 | The main process finished: wrong command |
| Liveness probe failures in events | The app is too slow to answer, or the probe is wrong |

## Startup errors

The application fails before it serves traffic, usually because of:

- missing or invalid configuration (an environment variable, a ConfigMap key),
- a missing Secret or file,
- a dependency it checks at startup (database, message broker) being unreachable,
- a bug in the new release.

Check whether the crashes started with a new revision. If they did, rolling
back is the quickest mitigation.

## Memory: OOMKilled

Compare the container's memory usage with its limit on the dashboard. If usage
climbs steadily until the kill, it is a leak; if it jumps at startup or under
load, the limit is too low for the workload. Raising the limit is a mitigation;
the leak still needs a fix.

## Probes

A liveness probe that fails makes Kubernetes restart a healthy-but-slow app.
Check the probe's path, port and timings against how long the app takes to
start. A readiness probe failure does not restart the container; it only removes
the pod from the Service.

## Mitigation

- Bad release: roll back.
- Bad configuration: restore the previous value.
- Memory limit: raise the limit, then investigate the usage.

All remediations require human approval.
