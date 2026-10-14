"""Is a rollback justified? Decided by rules, not by the model.

The model's rollback_recommended is one input. A rollback is proposed only when
the deployment evidence also supports it:
  - a revision was deployed shortly before the alert (a recent change exists),
  - it changed something real (image, environment, commit), not just a restart,
  - there is a previous revision to go back to,
  - nobody has changed the deployment since the alert (else re-check first).
The target revision comes from the evidence, never from the model's text.
"""

RECENT_CHANGE_SECONDS = 30 * 60
RESTART_ONLY = "annotation kubectl.kubernetes.io/restartedAt"


def _describe(revision: dict) -> str:
    commit = f", commit {revision['git_commit'][:7]}" if revision.get("git_commit") else ""
    return f"revision {revision['revision']} ({', '.join(revision.get('images', []))}{commit})"


def assess_rollback(deployment_evidence: dict, model_recommends: bool) -> dict:
    active = deployment_evidence.get("active_revision")
    previous = deployment_evidence.get("previous_revision")
    seconds = deployment_evidence.get("deployed_before_incident_seconds")
    supports, blockers = [], []

    if not active:
        blockers.append("no deployment revision is known for the time of the alert")
    else:
        if seconds is not None and seconds <= RECENT_CHANGE_SECONDS:
            supports.append(f"{_describe(active)} was deployed {seconds / 60:.1f} min before the alert")
        else:
            minutes = "unknown" if seconds is None else f"{seconds / 60:.0f}"
            blockers.append(f"the running revision was deployed {minutes} min before the alert: "
                            "no recent change to undo")
        changes = [c for c in active.get("changes_from_previous", []) if not c.startswith(RESTART_ONLY)]
        if changes:
            supports.append(f"it changed: {'; '.join(changes)}")
        else:
            blockers.append(f"revision {active['revision']} changed nothing but a restart: "
                            "rolling back would not undo a change")
    if not previous:
        blockers.append("there is no previous revision to roll back to")
    after = deployment_evidence.get("revisions_after_incident") or []
    if after:
        blockers.append(f"the deployment changed after the alert (revision "
                        f"{', '.join(str(r['revision']) for r in after)}): re-check before acting")

    justified = model_recommends and not blockers
    if model_recommends and blockers:
        note = "The model recommended a rollback, but the deployment evidence does not support it."
    elif not model_recommends and not blockers:
        note = "The model did not recommend a rollback, although a recent change could be undone."
    else:
        note = "The model's recommendation and the deployment evidence agree."

    return {
        "justified": justified,
        "model_recommended": model_recommends,
        "supporting": supports,
        "blockers": blockers,
        "note": note,
        "from_revision": active.get("revision") if active else None,
        "to_revision": previous.get("revision") if previous else None,
        "to_description": _describe(previous) if previous else None,
        "from_description": _describe(active) if active else None,
    }


def rollback_proposal(namespace: str, deployment: str, assessment: dict, analysis: dict) -> dict:
    """The exact action a human is asked to approve: one deployment, one target revision."""
    return {
        "action": "rollback_deployment",
        "namespace": namespace,
        "deployment": deployment,
        "from_revision": assessment["from_revision"],
        "to_revision": assessment["to_revision"],
        "summary": f"Roll back {deployment} in {namespace} from {assessment['from_description']} "
                   f"to {assessment['to_description']}.",
        "reason": analysis.get("rollback_reason", ""),
        "evidence": assessment["supporting"],
        "risk": "Pods are replaced one at a time (rolling update). Any change made after the target "
                "revision is undone, including configuration changes in the pod template.",
        "expected_impact": "If the rolled-back revision caused the incident, the error rate returns to its "
                           "baseline within about two minutes and the alert resolves.",
        "verification": "Error rate back to baseline and HighErrorRate resolved; no new error lines.",
    }
