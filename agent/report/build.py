"""Verify an analysis and assemble the IncidentReport."""

from datetime import UTC, datetime

from report.schema import EvidenceItem, IncidentReport, ReportStatus, Verification
from report.verify import check_fact, detail_issues, normalize
from schemas import RootCauseAnalysis

# Unsupported claims lower how much the analysis can be trusted.
CAP_UNVERIFIED_FACTS = 0.6  # some facts could not be found in the evidence
CAP_UNVERIFIED_ROOT_CAUSE = 0.5  # the root cause itself names details not in the evidence


def build_corpus(*texts: str) -> str:
    return normalize("\n".join(texts))


def check_analysis(analysis: RootCauseAnalysis, corpus: str) -> tuple[list[list[str]], list[str], list[str]]:
    """Issues per fact (same order as analysis.facts), in the root cause, and in the
    other free text (summary, inferences). Free text needs no quote, but its
    specific details must be in the evidence: the live run's summary cited a
    "line 21" that does not exist."""
    statements = [f"summary: {i}" for i in detail_issues(analysis.summary, corpus)]
    for inference in analysis.inferences:
        statements += [f'inference "{inference}": {i}' for i in detail_issues(inference, corpus)]
    return ([check_fact(f, corpus) for f in analysis.facts], detail_issues(analysis.root_cause, corpus), statements)


def problems_for_correction(analysis: RootCauseAnalysis, fact_issues: list[list[str]],
                            root_issues: list[str], statement_issues: list[str]) -> list[str]:
    problems = [f'fact "{f.statement}": {"; ".join(issues)}'
                for f, issues in zip(analysis.facts, fact_issues) if issues]
    if root_issues:
        problems.append(f'root cause "{analysis.root_cause}": {"; ".join(root_issues)}')
    return problems + statement_issues


def capped_confidence(confidence: float, fact_issues: list[list[str]], root_issues: list[str],
                      statement_issues: list[str] = ()) -> tuple[float, str | None]:
    unverified = sum(1 for issues in fact_issues if issues) + len(statement_issues)
    if root_issues and confidence > CAP_UNVERIFIED_ROOT_CAUSE:
        return CAP_UNVERIFIED_ROOT_CAUSE, (f"capped from {confidence:.2f}: the root cause names details "
                                           "that are not in the evidence")
    if unverified and confidence > CAP_UNVERIFIED_FACTS:
        return CAP_UNVERIFIED_FACTS, (f"capped from {confidence:.2f}: {unverified} claim(s) could not be "
                                      "verified against the evidence")
    return confidence, None


def report_status(state: dict) -> ReportStatus:
    if not state.get("remediation_proposal"):
        return "investigated"
    return {"approved": "remediation_approved", "rejected": "remediation_rejected"}.get(
        state.get("approval_status"), "awaiting_approval")


def build_report(state: dict, model: str) -> IncidentReport:
    analysis = RootCauseAnalysis.model_validate(state["analysis"])
    incident, assessment = state["incident"], state["rollback_assessment"]
    fact_issues = state["fact_issues"]

    evidence = [EvidenceItem(kind="fact", statement=f.statement, source=f.source, quote=f.quote,
                             verified=not issues, issues=issues)
                for f, issues in zip(analysis.facts, fact_issues)]
    evidence += [EvidenceItem(kind="inference", statement=i) for i in analysis.inferences]

    # The rollback decision is the rules' (nodes/rollback.py), not the model's.
    reasons = assessment["supporting"] if assessment["justified"] else assessment["blockers"]
    rollback_reason = f"{assessment['note']} " + "; ".join(reasons)
    if assessment["justified"] and analysis.rollback_reason:
        rollback_reason += f". Model: {analysis.rollback_reason}"

    remediation = None
    if state.get("remediation_proposal"):
        remediation = {"proposal": state["remediation_proposal"], "approval_status": state.get("approval_status"),
                       "approval": state.get("approval"), "result": state.get("remediation_result")}

    return IncidentReport(
        incident_id=state["incident_id"], severity=incident.get("severity"),
        service=state["service"], namespace=state["namespace"],
        summary=analysis.summary, root_cause=analysis.root_cause, evidence=evidence,
        confidence=state["confidence"], recommended_actions=analysis.recommended_actions,
        rollback_recommended=assessment["justified"], rollback_reason=rollback_reason.strip(),
        status=report_status(state), remediation=remediation,
        knowledge_sources=analysis.knowledge_sources,
        verification=Verification(
            facts_total=len(fact_issues), facts_verified=sum(1 for i in fact_issues if not i),
            corrected=state.get("corrected", False), root_cause_issues=state.get("root_cause_issues", []),
            statement_issues=state.get("statement_issues", []),
            confidence_note=state.get("confidence_note")),
        model=model, generated_at=datetime.now(UTC),
    )
