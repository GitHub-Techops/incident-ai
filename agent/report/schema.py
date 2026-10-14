"""The structured incident report (CLAUDE.md section 21).

Who fills what:
  - from the alert (code):        incident_id, severity, service, namespace
  - from the model:               summary, root_cause, facts, inferences, recommended_actions
  - from the verification (code): each fact's `verified` and `issues`, the confidence cap
  - from the rules (code):        rollback_recommended, rollback_reason
  - from the workflow (code):     status, approval, remediation
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ReportStatus = Literal[
    "investigated",            # no automated action proposed; recommendations for the on-call engineer
    "awaiting_approval",       # a remediation is proposed and waits for a human
    "remediation_approved",    # approved (executed by the controlled tools from Milestone 17)
    "remediation_rejected",    # a human rejected the proposal
]


class EvidenceItem(BaseModel):
    kind: Literal["fact", "inference"]
    statement: str
    source: str | None = None  # alert | kubernetes | metrics | deployment | knowledge
    quote: str | None = None  # the evidence text that shows a fact
    verified: bool | None = None  # facts only: was the quote found and every detail in the evidence?
    issues: list[str] = Field(default_factory=list)


class Verification(BaseModel):
    facts_total: int
    facts_verified: int
    corrected: bool  # the model was asked once to fix unsupported claims
    root_cause_issues: list[str] = Field(default_factory=list)
    statement_issues: list[str] = Field(default_factory=list)  # summary / inferences naming details not in the evidence
    confidence_note: str | None = None  # why the confidence was capped, if it was


class IncidentReport(BaseModel):
    incident_id: str
    severity: str | None
    service: str
    namespace: str
    summary: str
    root_cause: str
    evidence: list[EvidenceItem]
    confidence: float = Field(ge=0, le=1)
    recommended_actions: list[str]
    rollback_recommended: bool
    rollback_reason: str
    status: ReportStatus
    remediation: dict | None = None  # the proposal, the decision and its result
    knowledge_sources: list[str] = Field(default_factory=list)
    verification: Verification
    model: str
    generated_at: datetime
