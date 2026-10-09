---
title: Database connection errors
type: runbook
services: [any]
alerts: [HighErrorRate]
tags: [database, postgresql, connection refused, timeout, configuration]
---

# Database connection errors

## Symptoms

Requests that need the database fail with 5xx, and the logs contain messages
such as:

- `connection refused`: nothing accepted the connection at that address
- `connection timed out`: the address did not answer at all
- `too many connections` / `pool exhausted`: the database or the pool is full
- `authentication failed`: wrong credentials

Endpoints that do not touch the database (for example `/health` when it is a
shallow check) keep working, so pods stay Ready.

## Connection refused: what it means

"Connection refused" is an immediate answer from the target host: the network
path works, but nothing is listening on that host and port. The usual causes:

1. The client is connecting to the wrong address: a wrong host name or a wrong
   port in the application's configuration.
2. The database process is down or restarting.
3. The database listens on a different interface or port than expected.

Compare the address in the error message with the address the database really
uses (see the service's architecture document). If they differ, the problem is
the client's configuration, not the database.

## Check the client configuration

Where does the application get its database address? Look in this order:

```bash
# Environment set directly on the deployment (overrides everything else)
kubectl get deployment <service> -n <namespace> \
  -o jsonpath='{.spec.template.spec.containers[0].env}'
# ConfigMaps it loads
kubectl get configmap -n <namespace> -l app.kubernetes.io/name=<service> -o yaml
```

If neither sets the address, the application uses the default compiled into
its code. Defaults change with new releases: check the code diff of the most
recent deployment for changes to database settings.

## Check whether the change came with a deployment

If the errors started right after a rollout, the new revision is the prime
suspect even when the database looks healthy. A new release can change the
database address, the driver, the pool size, or the queries. Compare the
alert start time with the rollout history and read the diff between the two
revisions' commits.

## Check the database side

If the client configuration is correct:

- Is the database running and accepting connections?
- Did it restart recently (look at its pods' restarts and events)?
- Is it at its connection limit?
- Did a NetworkPolicy or firewall change?

## Mitigation

- Wrong address introduced by a deployment: roll back to the previous
  revision, then fix the configuration or code in a new release.
- Wrong address introduced by a configuration change: restore the previous
  value and restart the deployment.
- Database down: restore the database; the application recovers on its own
  once connections succeed.
- Pool exhausted: scale cautiously; more replicas mean more connections.

All remediations require human approval.

## Prevent it next time

- Make the readiness check test the database connection, or alert on
  dependency errors, so the problem is visible beyond the error rate.
- Log the database address the application uses at startup.
- Treat changes to connection defaults as risky in code review.
