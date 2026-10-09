---
title: Incident response process
type: runbook
services: [any]
alerts: []
tags: [process, on-call, communication, postmortem, approval]
---

# Incident response process

## Roles

- Incident commander: owns the incident, makes decisions, keeps the timeline.
- Investigator(s): gather evidence and propose causes and fixes.
- Communicator: updates stakeholders on impact and progress.

In a small team one person may hold several roles, but the commander decides.

## Severity

| Severity | Meaning |
|---|---|
| critical | Customers are failing now (errors, outage, data at risk) |
| warning | Degraded or at risk, no broad customer impact yet |
| info | Worth knowing, no action needed now |

The `HighErrorRate` alert is `critical`.

## The investigation loop

1. Establish impact: what fails, for whom, since when.
2. Ask what changed: deployments, configuration, dependencies, traffic.
3. Collect evidence: metrics around the start time, logs, Kubernetes state,
   deployment history and code diff.
4. Form a hypothesis that explains all the evidence, not only part of it.
5. Mitigate, then confirm the hypothesis.

Separate facts from inferences in every update. "Errors rose from 0% to 60% at
09:35" is a fact. "The 09:34 deployment caused it" is an inference until it is
confirmed, for example by a rollback that fixes it.

## Remediation needs approval

Any action that changes the system (rollback, restart, scale, configuration
change) needs explicit approval from a human. The proposal must state:

- the action and its exact target (namespace, deployment, revision),
- the evidence that supports it,
- the risk and the expected impact,
- how success will be verified.

Automated remediation is limited to the `incident-lab` namespace. Deleting
namespaces or running arbitrary commands is never an automated action.

## Closing an incident

An incident is closed when the alert has resolved, the error rate is back to
baseline, and the fix has held for at least 10 minutes. Record the timeline,
root cause, evidence and resolution.

## Postmortem

Write a blameless postmortem for every critical incident:

- Summary and impact
- Timeline (detection, investigation, mitigation, recovery)
- Root cause, with the evidence that proves it
- What went well, what went badly, where we got lucky
- Action items with owners

Postmortems are stored in `knowledge/incidents/` so future investigations can
find similar incidents.
