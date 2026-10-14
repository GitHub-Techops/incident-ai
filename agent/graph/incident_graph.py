"""The incident investigation as a LangGraph workflow.

    START -> receive_alert -+-> collect_kubernetes -+-> search_knowledge -> analyze -> verify_claims
                            +-> collect_metrics ----+                                       |
                            +-> collect_deployment -+                                assess_rollback
                                                                                           |
                                                                                  propose_remediation
                                                                                   /               \\
                                                                       (no action)                human_approval (pauses)
                                                                            |                     /              \\
                                                                            |              reject          approve: remediate
                                                                            v                 v                    v
                                                                         finalize_report <----+--------------------+ -> END

Why a graph instead of one function: each step is a node with a typed input
and output, the three evidence collectors run in parallel, the routing rules
are explicit, and the checkpointer saves the state after every step, so the
run can stop at human_approval and continue when someone decides, even much
later. Every path that gets past the analysis ends in finalize_report, so
every investigation produces an IncidentReport.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from nodes.incident_nodes import Deps, make_nodes
from state.incident_state import IncidentState

EVIDENCE_NODES = ["collect_kubernetes", "collect_metrics", "collect_deployment"]


def after_alert(state: IncidentState) -> list[str] | str:
    return END if state.get("stop_reason") else EVIDENCE_NODES  # fan out: all three run in parallel


def after_analysis(state: IncidentState) -> str:
    return END if state.get("stop_reason") else "verify_claims"


def after_proposal(state: IncidentState) -> str:
    return "human_approval" if state.get("remediation_proposal") else "finalize_report"


def after_approval(state: IncidentState) -> str:
    return "remediate" if state.get("approval_status") == "approved" else "finalize_report"


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
    nodes = make_nodes(deps)
    graph = StateGraph(IncidentState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "receive_alert")
    graph.add_conditional_edges("receive_alert", after_alert, EVIDENCE_NODES + [END])
    graph.add_edge(EVIDENCE_NODES, "search_knowledge")  # join: waits for all three
    graph.add_edge("search_knowledge", "analyze")
    graph.add_conditional_edges("analyze", after_analysis, ["verify_claims", END])
    graph.add_edge("verify_claims", "assess_rollback")
    graph.add_edge("assess_rollback", "propose_remediation")
    graph.add_conditional_edges("propose_remediation", after_proposal, ["human_approval", "finalize_report"])
    graph.add_conditional_edges("human_approval", after_approval, ["remediate", "finalize_report"])
    graph.add_edge("remediate", "finalize_report")
    graph.add_edge("finalize_report", END)

    # The checkpointer is what makes pausing for a human possible.
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
