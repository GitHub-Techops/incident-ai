"""The whole graph with a fake backend, retriever and model: routing, parallel
evidence, the pause for approval and both decisions."""

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.retrievers import BaseRetriever
from langgraph.types import Command

from graph.incident_graph import build_graph
from nodes.incident_nodes import Deps
from schemas import RootCauseAnalysis
from tests.test_evidence_format import k8s_evidence
from tests.test_rollback import evidence as deployment_evidence
from tools.backend import BackendClient, BackendError

ID = "INC-20261014-f2edbc"
INCIDENT = {"incident_id": ID, "alert_name": "HighErrorRate", "service": "incident-demo",
            "namespace": "incident-lab", "severity": "critical", "status": "open",
            "summary": "High HTTP 5xx error rate on incident-demo", "description": "66% failing",
            "alert": {"startsAt": "2026-10-14T08:51:15Z", "labels": {"alertname": "HighErrorRate"}}}
METRICS = {"window_start": "a", "window_end": "b", "incident_time": "c", "resolved_time": None,
           "metrics": [], "errors": []}


class FakeBackend(BackendClient):
    def __init__(self, incident=INCIDENT, deployment=None, fail=()):
        super().__init__("http://unused")
        self.incident = incident
        self.evidence = {"kubernetes": k8s_evidence(), "metrics": METRICS,
                         "deployment": deployment or {**deployment_evidence(), "facts": ["v2 deployed"], "errors": []}}
        self.fail = set(fail)
        self.calls: list[str] = []

    def get_incident(self, incident_id):
        return self.incident

    def collect_evidence(self, incident_id, kind):
        self.calls.append(kind)
        if kind in self.fail:
            raise BackendError("HTTP 503")
        return self.evidence[kind]


class FakeRetriever(BaseRetriever):
    queries: list = []

    def _get_relevant_documents(self, query, *, run_manager):
        self.queries.append(query)
        return [Document(page_content="runbook text", metadata={"source": "runbooks/database-errors.md",
                                                                  "title": "DB", "type": "runbook", "relevance": 0.79})]


class FakeModel:
    def __init__(self, rollback=True):
        self.rca = RootCauseAnalysis(
            summary="Orders fail.", facts=["db_host orders-db:5433"], inferences=["wrong default port"],
            root_cause="v2 defaults DB_PORT to 5433", confidence=0.9, recommended_actions=["roll back"],
            rollback_recommended=rollback, rollback_reason="v2 introduced it",
            knowledge_sources=["runbooks/database-errors.md"])
        self.prompts: list = []

    def with_structured_output(self, schema, include_raw=False):
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        return {"raw": AIMessage("{}"), "parsed": self.rca, "parsing_error": None}


def run(backend=None, model=None):
    model = model or FakeModel()
    graph = build_graph(Deps(backend=backend or FakeBackend(), retriever=FakeRetriever(), llm=model))
    config = {"configurable": {"thread_id": "t1"}}
    result = graph.invoke({"incident_id": ID}, config)
    return graph, config, result, model


def test_runs_to_the_approval_pause_with_a_proposal():
    graph, config, result, model = run()
    assert "__interrupt__" in result  # paused, waiting for a human
    request = result["__interrupt__"][0].value
    assert request["proposal"]["to_revision"] == 33
    state = graph.get_state(config)
    assert state.next == ("human_approval",)
    assert state.values["approval_status"] == "pending"
    # Evidence from all three collectors and the knowledge search reached the model.
    prompt = model.prompts[0][1].content
    assert "Kubernetes evidence" in prompt and "Metrics evidence" in prompt and "v2 deployed" in prompt
    assert "runbooks/database-errors.md" in prompt


def test_reject_ends_without_remediation():
    graph, config, _, _ = run()
    graph.invoke(Command(resume={"decision": "reject", "decided_by": "alice"}), config)
    state = graph.get_state(config)
    assert state.next == ()
    assert state.values["approval_status"] == "rejected"
    assert state.values["approval"]["decided_by"] == "alice"
    assert "remediation_result" not in state.values


def test_approve_records_the_action_without_executing_it():
    graph, config, _, _ = run()
    graph.invoke(Command(resume={"decision": "approve", "decided_by": "bob"}), config)
    values = graph.get_state(config).values
    assert values["approval_status"] == "approved"
    assert values["remediation_result"]["status"] == "not_executed"
    assert "--to-revision=33" in values["remediation_result"]["manual_command"]


def test_no_justified_rollback_means_no_approval_step():
    old_deploy = {**deployment_evidence(deployed_before_incident_seconds=3 * 3600), "facts": [], "errors": []}
    graph, config, result, _ = run(backend=FakeBackend(deployment=old_deploy))
    assert "__interrupt__" not in result
    assert result["remediation_proposal"] is None
    assert result["rollback_assessment"]["blockers"]


def test_a_failed_collector_is_a_gap_not_a_crash():
    graph, config, result, model = run(backend=FakeBackend(fail={"metrics"}))
    assert any("metrics evidence" in e for e in result["errors"])
    assert "Evidence that could not be collected" in model.prompts[0][1].content
    assert result["root_cause"]  # the analysis still ran on what was collected


def test_namespace_outside_scope_is_refused_before_any_evidence():
    backend = FakeBackend(incident={**INCIDENT, "namespace": "kube-system"})
    graph, config, result, model = run(backend=backend)
    assert "outside the agent's scope" in result["stop_reason"]
    assert backend.calls == [] and model.prompts == []
