"""The investigation steps. Each node reads the state and returns only the keys it adds.

Evidence comes from the backend (which enforces the safety rules), knowledge
from Redis, analysis from the model (Milestone 14 chain). Nothing here changes
the cluster: the remediate node only records the approved action until the
controlled remediation tools exist (Milestone 17).
"""

import time
from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.retrievers import BaseRetriever
from langgraph.types import interrupt

from chains.rca import RCAError, analyze, most_telling_error, retrieval_queries, retrieve
from nodes.rollback import assess_rollback, rollback_proposal
from state.incident_state import IncidentState
from tools.backend import BackendClient, BackendError
from tools.evidence_format import format_alert, format_deployment, format_kubernetes, format_metrics
from tools.knowledge import format_documents

# The agent investigates only the lab namespace (CLAUDE.md safety rules).
ALLOWED_NAMESPACES = frozenset({"incident-lab"})


@dataclass
class Deps:
    backend: BackendClient
    retriever: BaseRetriever
    llm: BaseChatModel


def make_nodes(deps: Deps) -> dict[str, Any]:

    def receive_alert(state: IncidentState) -> dict:
        try:
            incident = deps.backend.get_incident(state["incident_id"])
        except BackendError as exc:
            return {"stop_reason": f"cannot load the incident: {exc}", "trace": ["receive_alert: failed"]}
        service, namespace = incident.get("service"), incident.get("namespace")
        if not service or not namespace:
            return {"incident": incident, "stop_reason": "the alert has no service/namespace labels",
                    "trace": ["receive_alert: no service to investigate"]}
        if namespace not in ALLOWED_NAMESPACES:
            return {"incident": incident, "stop_reason": f"namespace {namespace} is outside the agent's scope",
                    "trace": [f"receive_alert: refused namespace {namespace}"]}
        return {"incident": incident, "service": service, "namespace": namespace, "approval_status": "not_required",
                "trace": [f"receive_alert: {incident.get('alert_name')} on {namespace}/{service}, "
                          f"status {incident.get('status')}"]}

    def collector(kind: str, describe):
        def collect(state: IncidentState) -> dict:
            start = time.perf_counter()
            try:
                evidence = deps.backend.collect_evidence(state["incident_id"], kind)
            except BackendError as exc:
                return {"errors": [f"{kind} evidence: {exc}"], "trace": [f"collect_{kind}: failed"]}
            gaps = [f"{kind}: {e}" for e in evidence.get("errors", [])]
            return {f"{kind}_evidence": evidence, "errors": gaps,
                    "trace": [f"collect_{kind}: {describe(evidence)} ({time.perf_counter() - start:.1f}s)"]}
        collect.__name__ = f"collect_{kind}"
        return collect

    collect_kubernetes = collector("kubernetes", lambda e: (
        f"{len(e.get('pods', []))} pods, "
        f"{sum(l.get('error_line_count', 0) for l in e.get('logs', []))} error log lines"))
    collect_metrics = collector("metrics", lambda e: f"{len(e.get('metrics', []))} metrics")
    collect_deployment = collector("deployment", lambda e: (
        f"active revision {(e.get('active_revision') or {}).get('revision')}, {len(e.get('commits', []))} commits"))

    def search_knowledge(state: IncidentState) -> dict:
        logs = (state.get("kubernetes_evidence") or {}).get("logs", [])
        error = most_telling_error([line for l in logs for line in l.get("error_lines", [])])
        queries = retrieval_queries(state["incident"].get("summary") or "", error)
        try:
            docs = retrieve(deps.retriever, queries)
        except Exception as exc:  # noqa: BLE001 - knowledge is helpful, not required
            return {"rag_evidence": [], "errors": [f"knowledge search: {exc}"], "trace": ["search_knowledge: failed"]}
        rag = [{**d.metadata, "content": d.page_content} for d in docs]
        return {"rag_evidence": rag,
                "trace": [f"search_knowledge: {', '.join(f'{d['source']} ({d['relevance']:.2f})' for d in rag)}"]}

    def analyze_evidence(state: IncidentState) -> dict:
        parts = []
        for key, fmt in (("kubernetes_evidence", format_kubernetes), ("metrics_evidence", format_metrics),
                         ("deployment_evidence", format_deployment)):
            if state.get(key):
                parts.append(fmt(state[key]))
        if state.get("errors"):
            parts.append("## Evidence that could not be collected\n" + "\n".join(f"- {e}" for e in state["errors"]))
        knowledge = format_documents([Document(page_content=d["content"], metadata=d)
                                      for d in state.get("rag_evidence", [])]) or "(none)"
        start = time.perf_counter()
        try:
            result, attempts = analyze(deps.llm, format_alert(state["incident"]), "\n\n".join(parts), knowledge)
        except RCAError as exc:
            return {"stop_reason": f"analysis failed: {exc}", "trace": ["analyze: failed"]}
        return {
            "analysis": result.model_dump(), "root_cause": result.root_cause, "confidence": result.confidence,
            "recommended_actions": result.recommended_actions, "rollback_recommended": result.rollback_recommended,
            "trace": [f"analyze: confidence {result.confidence:.2f}, rollback "
                      f"{'recommended' if result.rollback_recommended else 'not recommended'} "
                      f"({time.perf_counter() - start:.1f}s, {attempts} attempt(s))"],
        }

    def assess_rollback_node(state: IncidentState) -> dict:
        assessment = assess_rollback(state.get("deployment_evidence") or {}, state.get("rollback_recommended", False))
        verdict = "justified" if assessment["justified"] else "not justified"
        return {"rollback_assessment": assessment,
                "trace": [f"assess_rollback: {verdict}; {assessment['note']}"]}

    def propose_remediation(state: IncidentState) -> dict:
        assessment = state["rollback_assessment"]
        if not assessment["justified"]:
            return {"remediation_proposal": None, "approval_status": "not_required",
                    "trace": ["propose_remediation: no automated action; recommendations go to the on-call engineer"]}
        proposal = rollback_proposal(state["namespace"], state["service"], assessment, state["analysis"])
        return {"remediation_proposal": proposal, "approval_status": "pending",
                "trace": [f"propose_remediation: {proposal['summary']}"]}

    def human_approval(state: IncidentState) -> dict:
        # The graph stops here and saves its state (checkpointer). It continues
        # only when a human answers: Command(resume={"decision": ..., ...}).
        answer = interrupt({
            "incident_id": state["incident_id"],
            "root_cause": state["root_cause"],
            "confidence": state["confidence"],
            "proposal": state["remediation_proposal"],
            "question": "Approve this remediation? Answer approve or reject.",
        })
        decision = str(answer.get("decision", "")).lower()
        status = "approved" if decision == "approve" else "rejected"
        approval = {"decided_by": answer.get("decided_by", "unknown"), "comment": answer.get("comment")}
        return {"approval_status": status, "approval": approval,
                "trace": [f"human_approval: {status} by {approval['decided_by']}"]}

    def remediate(state: IncidentState) -> dict:
        proposal = state["remediation_proposal"]
        # Milestone 17 replaces this with the controlled rollback_deployment() tool.
        return {"remediation_result": {
                    "status": "not_executed", "action": proposal["action"],
                    "reason": "approved, but the controlled remediation tools arrive in Milestone 17",
                    "manual_command": f"kubectl rollout undo deployment/{proposal['deployment']} "
                                      f"-n {proposal['namespace']} --to-revision={proposal['to_revision']}"},
                "trace": ["remediate: approved action recorded, not executed (Milestone 17)"]}

    return {
        "receive_alert": receive_alert,
        "collect_kubernetes": collect_kubernetes,
        "collect_metrics": collect_metrics,
        "collect_deployment": collect_deployment,
        "search_knowledge": search_knowledge,
        "analyze": analyze_evidence,
        "assess_rollback": assess_rollback_node,
        "propose_remediation": propose_remediation,
        "human_approval": human_approval,
        "remediate": remediate,
    }
