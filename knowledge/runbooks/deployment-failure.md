---
title: Bad deployment and rollback
type: runbook
services: [any]
alerts: [HighErrorRate]
tags: [deployment, release, rollback, rollout, change]
---

# Bad deployment and rollback

## When to suspect a deployment

A deployment is the most likely cause of an incident when:

- the alert started within minutes of a new revision rolling out,
- the errors appear only on pods of the new ReplicaSet (or on all pods once the
  rollout finished),
- the error messages are new, not seen before the release.

Correlation is not proof: also check what else changed at the same time
(configuration, dependencies, traffic).

## Identify what changed

```bash
kubectl rollout history deployment/<service> -n <namespace>
kubectl rollout history deployment/<service> -n <namespace> --revision=<n>
```

Each revision shows its image and pod template. For revisions deployed through
the release script, the pod template annotations record:

- `incident-ai.dev/git-commit`: the exact commit that was built
- `incident-ai.dev/git-ref`: the release tag, e.g. `demo-app/v2`
- `incident-ai.dev/deployed-at`: when the release was deployed

The code that changed is the Git diff between the previous revision's commit
and the new one. Read the commit messages and the diff for anything that
touches the failing code path or its configuration.

Beware of the CHANGE-CAUSE column: Kubernetes copies a deployment's
change-cause annotation onto later revisions, so it can describe an older
change. Trust the actual differences (image, environment, annotations).

## Kinds of changes

- Image changed: new code. Look at the commits and the diff.
- Environment variables changed, same image: a configuration change.
- Only the `restartedAt` annotation changed: a restart. Restarts pick up
  changed ConfigMap or Secret contents, so check those too.

## Roll back

Rolling back is usually the fastest safe mitigation when a release caused the
incident. Options:

```bash
# Redeploy the previous release from its tag (records commit and time)
scripts/deploy-demo.sh <previous-version>
# or the Kubernetes built-in rollback
kubectl rollout undo deployment/<service> -n <namespace>
```

`kubectl rollout undo` reuses the old ReplicaSet, so that revision keeps its
original creation time. Redeploying from the tag creates a new revision with
an accurate deployment time.

Rollbacks require human approval. Check first that the previous release was
healthy, and that the new release made no one-way change (for example a
database migration) that the old code cannot handle.

## After the rollback

- Verify: the error rate returns to baseline and the alert resolves.
- Keep the bad release out of production until the root cause is fixed.
- Write a postmortem: what changed, why it broke, why tests and review did not
  catch it.
