"""LangChain tools the model can call while answering questions about one incident.

A tool = a Python function + a name + a description. The model reads the
descriptions, decides which tool to call, and LangChain runs the function and
hands the result back. The model only *asks*; our code executes, and only these
functions exist: there is no shell and no Kubernetes access behind them.

The tools are bound to one incident id when they're created, so the model can't
point them at a different incident (or make one up).
"""

from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool, tool

from tools.backend import BackendClient, BackendError
from tools.evidence_format import format_deployment, format_kubernetes, format_metrics
from tools.knowledge import format_documents


def make_incident_tools(client: BackendClient, incident_id: str, retriever: BaseRetriever) -> list[BaseTool]:

    def collect(kind: str, formatter) -> str:
        try:
            return formatter(client.collect_evidence(incident_id, kind))
        except BackendError as exc:
            # Returned as text, not raised: the model sees the gap and can say so.
            return f"Could not collect {kind} evidence: {exc}"

    @tool
    def get_kubernetes_evidence() -> str:
        """Current Kubernetes state of the affected service: pods (ready, restarts), recent error log
        lines, events, the deployment and its environment, rollout history, services and ConfigMaps."""
        return collect("kubernetes", format_kubernetes)

    @tool
    def get_metrics_evidence() -> str:
        """Prometheus metrics around the incident: request rate, error rate, p95 latency, CPU and memory,
        each summarized before the alert (baseline), during it, and after recovery."""
        return collect("metrics", format_metrics)

    @tool
    def get_deployment_evidence() -> str:
        """What changed before the incident: the deployment revision running when the alert fired,
        the one it replaced, and the Git commits and code diff between them."""
        return collect("deployment", format_deployment)

    @tool
    def search_knowledge(query: str) -> str:
        """Search the team's runbooks, troubleshooting guides, architecture docs and past incident
        postmortems. Use a short description of the symptom, e.g. 'database connection refused'."""
        docs = retriever.invoke(query)
        return format_documents(docs) if docs else "No matching documents."

    return [get_kubernetes_evidence, get_metrics_evidence, get_deployment_evidence, search_knowledge]
