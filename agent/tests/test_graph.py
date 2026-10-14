"""The whole graph with a fake backend, retriever and model: routing, parallel
evidence, the pause for approval and both decisions."""

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.retrievers import BaseRetriever
from langgraph.types import Command

from graph.incident_graph import build_graph
from nodes.incident_nodes import Deps
from report.schema import IncidentReport
from schemas import Fact, RootCauseAnalysis
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


TRUE_FACT = Fact(statement="Requests fail with database connection refused.", source="kubernetes",
                 quote='"error": "database connection refused"')
INVENTED = Fact(statement="The port is set on line 18.", source="deployment",
                quote='"error": "database connection refused"')


def rca(facts=(TRUE_FACT,), rollback=True):
    return RootCauseAnalysis(
        summary="Orders fail.", facts=list(facts), inferences=["wrong default port"],
        root_cause="v2 defaults DB_PORT to 5433", confidence=0.9, recommended_actions=["roll back"],
        rollback_recommended=rollback, rollback_reason="v2 introduced it",
        knowledge_sources=["runbooks/database-errors.md"])


class FakeModel:
    """Returns the scripted answers in order (the last one repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers) or [rca()]
        self.prompts: list = []

    def with_structured_output(self, schema, include_raw=False):
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return {"raw": AIMessage("{}"), "parsed": answer, "parsing_error": None}


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


def test_reject_ends_with_a_rejected_report():
    graph, config, _, _ = run()
    graph.invoke(Command(resume={"decision": "reject", "decided_by": "alice"}), config)
    state = graph.get_state(config)
    assert state.next == ()
    assert state.values["approval"]["decided_by"] == "alice"
    assert "remediation_result" not in state.values
    report = IncidentReport.model_validate(state.values["report"])
    assert report.status == "remediation_rejected"
    assert report.remediation["approval"]["decided_by"] == "alice"


def test_approve_records_the_action_without_executing_it():
    graph, config, _, _ = run()
    graph.invoke(Command(resume={"decision": "approve", "decided_by": "bob"}), config)
    values = graph.get_state(config).values
    assert values["remediation_result"]["status"] == "not_executed"
    assert "--to-revision=33" in values["remediation_result"]["manual_command"]
    assert values["report"]["status"] == "remediation_approved"


def test_report_fields_come_from_the_right_owner():
    graph, config, _, _ = run()
    graph.invoke(Command(resume={"decision": "reject", "decided_by": "alice"}), config)
    report = IncidentReport.model_validate(graph.get_state(config).values["report"])
    # From the alert, not the model.
    assert (report.incident_id, report.severity, report.service, report.namespace) == \
        (ID, "critical", "incident-demo", "incident-lab")
    # Facts and inferences kept apart; the true fact is verified.
    facts = [e for e in report.evidence if e.kind == "fact"]
    assert [(f.statement, f.verified) for f in facts] == [(TRUE_FACT.statement, True)]
    assert [e.statement for e in report.evidence if e.kind == "inference"] == ["wrong default port"]
    assert report.verification.facts_verified == 1 and report.confidence == 0.9


def test_no_justified_rollback_ends_with_an_investigated_report():
    old_deploy = {**deployment_evidence(deployed_before_incident_seconds=3 * 3600), "facts": [], "errors": []}
    graph, config, result, _ = run(backend=FakeBackend(deployment=old_deploy))
    assert "__interrupt__" not in result
    assert result["remediation_proposal"] is None
    report = IncidentReport.model_validate(result["report"])
    assert report.status == "investigated"
    # The model said "roll back"; the rules overrule it, and the report says why.
    assert report.rollback_recommended is False
    assert "no recent change to undo" in report.rollback_reason


def test_unsupported_claim_is_sent_back_and_corrected():
    model = FakeModel(rca(facts=[TRUE_FACT, INVENTED]), rca())
    graph, config, result, _ = run(model=model)
    state = graph.get_state(config).values
    assert len(model.prompts) == 2  # analysis + one correction round
    assert "'line 18' does not appear in the evidence" in model.prompts[1][-1].content
    assert state["corrected"] and state["fact_issues"] == [[]]
    assert state["confidence"] == 0.9  # nothing left unsupported: no cap


def test_claim_still_unsupported_after_correction_is_flagged_and_caps_confidence():
    model = FakeModel(rca(facts=[TRUE_FACT, INVENTED]))  # repeats the same mistake
    graph, config, result, _ = run(model=model)
    graph.invoke(Command(resume={"decision": "reject", "decided_by": "alice"}), config)
    report = IncidentReport.model_validate(graph.get_state(config).values["report"])
    flagged = [e for e in report.evidence if e.kind == "fact" and not e.verified]
    assert [f.statement for f in flagged] == [INVENTED.statement]
    assert flagged[0].issues == ["'line 18' does not appear in the evidence"]
    assert report.confidence == 0.6 and "could not be verified" in report.verification.confidence_note
    assert result["__interrupt__"][0].value["facts_verified"] == "1/2"  # the approver sees it too


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
