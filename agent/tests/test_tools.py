"""The backend client and the LangChain tools, with a fake backend and retriever."""

import pytest
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from tools.backend import BackendClient, BackendError
from tools.incident_tools import make_incident_tools

INCIDENT = "INC-20261014-1a2b3c"


@pytest.mark.parametrize("bad", ["../../admin", "INC-1", "INC-20261014-1a2b3c/evidence", ""])
def test_client_only_accepts_real_incident_ids(bad):
    with pytest.raises(BackendError, match="not an incident id"):
        BackendClient("http://unused").collect_evidence(bad, "kubernetes")


def test_client_only_accepts_known_evidence_kinds():
    with pytest.raises(BackendError, match="unknown evidence kind"):
        BackendClient("http://unused").collect_evidence(INCIDENT, "secrets")


class FakeBackend(BackendClient):
    def __init__(self, fail: bool = False):
        super().__init__("http://unused")
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def collect_evidence(self, incident_id, kind):
        self.calls.append((incident_id, kind))
        if self.fail:
            raise BackendError("HTTP 403 namespace not allowed")
        return {"facts": ["revision 25 was deployed 1m17s before the alert."], "errors": []}


class FakeRetriever(BaseRetriever):
    def _get_relevant_documents(self, query, *, run_manager):
        return [Document(page_content=f"Database connection errors\n\nabout: {query}",
                         metadata={"source": "runbooks/database-errors.md", "relevance": 0.83,
                                   "title": "Database connection errors", "type": "runbook"})]


def tools_by_name(backend):
    return {t.name: t for t in make_incident_tools(backend, INCIDENT, FakeRetriever())}


def test_tools_have_names_and_descriptions_the_model_can_read():
    tools = tools_by_name(FakeBackend())
    assert set(tools) == {"get_kubernetes_evidence", "get_metrics_evidence",
                          "get_deployment_evidence", "search_knowledge"}
    assert all(len(t.description) > 50 for t in tools.values())
    assert tools["get_deployment_evidence"].args == {}  # nothing for the model to fill in
    assert set(tools["search_knowledge"].args) == {"query"}


def test_evidence_tools_are_bound_to_one_incident():
    backend = FakeBackend()
    text = tools_by_name(backend)["get_deployment_evidence"].invoke({})
    assert backend.calls == [(INCIDENT, "deployment")]
    assert "deployed 1m17s before the alert" in text


def test_backend_errors_reach_the_model_as_text():
    text = tools_by_name(FakeBackend(fail=True))["get_kubernetes_evidence"].invoke({})
    assert text.startswith("Could not collect kubernetes evidence") and "403" in text


def test_knowledge_results_carry_their_source():
    text = tools_by_name(FakeBackend())["search_knowledge"].invoke({"query": "connection refused"})
    assert "### Source: runbooks/database-errors.md (relevance 0.83)" in text
