---
title: "INC-20261007-f15a0b: orders failing, database connection refused"
type: incident
services: [incident-demo]
alerts: [HighErrorRate]
tags: [database, connection refused, configuration, env override, postmortem]
date: 2026-10-07
severity: critical
incident_id: INC-20261007-f15a0b
root_cause_category: configuration change
---

# INC-20261007-f15a0b: orders failing, database connection refused

## Summary

For about 3.5 minutes, every order on `incident-demo` failed with HTTP 500.
The environment variable `FAIL_MODE=true` had been set directly on the
`incident-demo` Deployment, which makes the orders database unreachable. The
service's pods stayed Running and Ready throughout. Removing the variable
fixed it. This was a controlled drill in the lab, with a known cause.

## Impact

- `/api/order` failed for every request; `/health` and `/` were unaffected.
- Overall 5xx ratio: 0.0% before, 56.2% during, 0.0% after.
- p95 latency of `/api/order`: 48 ms before, 436 ms during, 49 ms after.
  `/health` stayed at 5 ms.

## Timeline (UTC)

| Time | Event |
|---|---|
| 09:34:33 | `kubectl set env deployment/incident-demo -n incident-lab FAIL_MODE=true`: new revision rolls out |
| 09:35:45 | `HighErrorRate` starts firing |
| 09:35:57 | Incident created from the Alertmanager webhook |
| 09:36:59 | `FAIL_MODE` removed from the Deployment (mitigation) |
| 09:37:58 | Alert resolved |

Time to detect: about 70 seconds from the change to the alert. Time to recover:
about 1 minute from the fix to the alert resolving.

## Evidence

- Logs: repeated ERROR lines `order failed` with
  `error: database connection refused` and `db_host: orders-db:5432`.
- Kubernetes: 2 pods Running and Ready, 0 restarts. The latest revision's only
  change was `env FAIL_MODE: <unset> -> true`; the image and Git commit were
  unchanged.
- Configuration: the ConfigMap `incident-demo-config` said `FAIL_MODE: "false"`,
  but the variable set directly on the Deployment overrides the ConfigMap.
- Metrics: the error ratio and the `/api/order` latency rose together and fell
  together, with no change on `/health`.

## Root cause

A configuration change, not a code change: `FAIL_MODE=true` set directly on the
Deployment made the application refuse database connections. The database
address in the logs (`orders-db:5432`) was the correct one.

## Why it was confusing

- Pods looked healthy because `/health` does not check the database.
- The ConfigMap showed the "right" value, which hides the override unless the
  Deployment's own environment is checked as well.

## Resolution

Removed the override:
`kubectl set env deployment/incident-demo -n incident-lab FAIL_MODE-`.
Errors stopped as soon as the new pods were ready.

## Lessons

- For database errors, check both the ConfigMap and the Deployment's own
  environment; the Deployment wins.
- A revision that changes only environment variables is a configuration change:
  the code diff is empty. Rolling back the revision restores the environment
  too, but redeploying the same image with the override still in place would
  not help.
- Compare the database address in the logs with the documented one: here it
  matched, which ruled out a wrong address.
