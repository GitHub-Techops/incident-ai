"""The incident investigation's state: one object passed from node to node.

Think of it as the incident ticket: the alert node fills in what broke, each
evidence node attaches its findings, the analysis node adds the root cause,
and the approval node records the human decision. Nodes return only the keys
they change; LangGraph merges them into the state.

Two keys are lists that several nodes append to (errors, trace). Their
`operator.add` annotation tells LangGraph to concatenate updates instead of
replacing them, which matters when the evidence nodes run in parallel.
"""

import operator
from typing import Annotated, Any, Literal, TypedDict

ApprovalStatus = Literal["not_required", "pending", "approved", "rejected"]


class IncidentState(TypedDict, total=False):
    incident_id: str
    incident: dict[str, Any]  # as returned by the backend: alert, labels, timeline
    service: str
    namespace: str

    kubernetes_evidence: dict[str, Any]
    metrics_evidence: dict[str, Any]
    deployment_evidence: dict[str, Any]
    rag_evidence: list[dict[str, Any]]  # source, title, relevance, content

    analysis: dict[str, Any]  # RootCauseAnalysis
    root_cause: str
    confidence: float
    recommended_actions: list[str]
    rollback_recommended: bool  # what the model said
    rollback_assessment: dict[str, Any]  # what the rules say, and why
    remediation_proposal: dict[str, Any] | None

    approval_status: ApprovalStatus
    approval: dict[str, Any]  # decided_by, comment
    remediation_result: dict[str, Any]

    stop_reason: str  # set when the investigation cannot continue
    errors: Annotated[list[str], operator.add]  # gaps in the evidence, never fatal on their own
    trace: Annotated[list[str], operator.add]  # one line per node: what it did
