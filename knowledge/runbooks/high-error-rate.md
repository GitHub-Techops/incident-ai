---
title: High HTTP error rate
type: runbook
services: [any]
alerts: [HighErrorRate]
tags: [http, 5xx, errors, availability]
---

# High HTTP error rate

## When this applies

The `HighErrorRate` alert fires when more than 5% of a service's HTTP requests
return a 5xx status for at least 30 seconds. Users are seeing failures right
now: treat it as customer-impacting until shown otherwise.

5xx means the service itself failed (500 internal error, 502/503/504 gateway or
availability problems). 4xx errors are client mistakes and are not counted.

## First five minutes

1. Confirm the alert is real: open the service's Grafana dashboard and check the
   "HTTP 5xx error rate" panel. A sustained plateau is an incident; a single
   spike that already recovered may only need a note.
2. Find the scope: which endpoints fail? Check "Requests per second by path and
   status". If only one endpoint fails, the problem is in that code path or its
   dependency. If every endpoint fails, suspect the whole service, its node or
   its network.
3. Ask "what changed?" before anything else. Most incidents follow a change: a
   deployment, a configuration change, a feature flag, or a dependency change.
   See "Check recent changes" below.
4. Communicate: open the incident channel, state the impact (error rate,
   endpoints affected) and that investigation is under way.

## Check recent changes

Compare the alert start time with the rollout history:

```bash
kubectl rollout history deployment/<service> -n <namespace>
kubectl get rs -n <namespace> -l app.kubernetes.io/name=<service> \
  --sort-by=.metadata.creationTimestamp
```

A revision created a few minutes before the alert is the prime suspect. Look at
what it changed: image (new code), environment variables (configuration), or
only an annotation (a restart). Revisions deployed with `deploy-demo.sh` record
their Git commit, so the code diff between the two revisions is available.

Note that changing the *contents* of a ConfigMap does not create a revision.
If the ConfigMap changed and the pods were restarted, the history only shows a
restart.

## Read the logs

```bash
kubectl logs deployment/<service> -n <namespace> --tail=100
```

Look for the error lines that come with the 5xx responses. The error message
usually names the failing dependency (database, downstream API) and often its
address. A burst of identical errors right after a deployment points at the
change; scattered different errors point at a dependency or capacity problem.

## Why pods can look healthy

Pods can be Running and Ready with zero restarts while most requests fail. This
happens when the health check is shallow: `/health` answers without testing
the database or other dependencies. Kubernetes then has no reason to restart
anything. Do not conclude "the service is fine" from pod status alone; trust
the error rate and the logs.

## Check latency together with errors

If latency rose at the same time as the errors, requests are waiting on
something before failing, typically a dependency that is slow or refusing
connections after a timeout. If errors are instant, the failure is immediate:
a bad configuration, a missing resource, a code exception.

## Decide on mitigation

Mitigate first, find the root cause after. Options, from least to most risky:

- The incident started right after a deployment: roll back to the previous
  revision. This is the fastest safe fix when the change is the likely cause.
- A configuration change caused it: revert the configuration (restore the
  previous environment variable or ConfigMap value) and restart.
- A dependency is down: the fix is on the dependency side; consider degrading
  the feature instead of failing every request.

Every remediation in this platform requires human approval, and automated
actions are restricted to the `incident-lab` namespace.

## Verify recovery

After the fix, the error rate should return to its baseline within one to two
minutes, and the alert resolves. Confirm on the dashboard and check that new
log lines no longer contain the error. Keep watching for 10 minutes before
closing the incident.
