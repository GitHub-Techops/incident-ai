"""Investigate an incident with the LangGraph workflow.

    python investigate.py INC-20261014-1a2b3c                   # asks you to approve/reject
    python investigate.py INC-... --decision reject --by alice  # non-interactive
    python investigate.py --graph                               # print the graph (Mermaid)

Needs the backend (kubectl port-forward -n incident-ai svc/incident-backend 8080:80),
Redis and Ollama (OLLAMA_URL).
"""

import argparse
import json
import sys
import uuid

from langgraph.types import Command

from graph.incident_graph import build_graph
from llm import make_chat_model
from nodes.incident_nodes import Deps
from settings import Settings
from tools.backend import BackendClient
from tools.knowledge import make_retriever


def run_until_pause(graph, payload, config) -> dict | None:
    """Stream node updates; return the interrupt payload if the graph paused for a human."""
    for chunk in graph.stream(payload, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                return update[0].value
            for line in (update or {}).get("trace", []):
                print(f"  {line}")
            for error in (update or {}).get("errors", []):
                print(f"    gap: {error}")
    return None


def ask_human(request: dict, decision: str | None, decided_by: str) -> dict:
    p = request["proposal"]
    print("\n=== APPROVAL REQUIRED ==========================================")
    print(f"Root cause ({request['confidence']:.2f}): {request['root_cause']}")
    print(f"Proposal:  {p['summary']}")
    print(f"Reason:    {p['reason']}")
    print("Evidence:  " + "\n           ".join(p["evidence"]))
    print(f"Risk:      {p['risk']}")
    print(f"Expected:  {p['expected_impact']}")
    print("================================================================")
    if decision is None:
        decision = input("approve / reject? ").strip().lower()
    return {"decision": decision, "decided_by": decided_by}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("incident_id", nargs="?")
    parser.add_argument("--decision", choices=["approve", "reject"], help="answer the approval without asking")
    parser.add_argument("--by", default="cli-user", help="who decides (recorded with the decision)")
    parser.add_argument("--reasoning", action="store_true", help="let the model think before answering")
    parser.add_argument("--graph", action="store_true", help="print the graph as a Mermaid diagram and exit")
    args = parser.parse_args()

    settings = Settings.from_env()
    deps = Deps(backend=BackendClient(settings.backend_url), retriever=make_retriever(settings),
                llm=make_chat_model(settings, reasoning=args.reasoning))
    graph = build_graph(deps)
    if args.graph:
        print(graph.get_graph().draw_mermaid())
        return
    if not args.incident_id:
        sys.exit("incident id required (or --graph)")

    # One thread = one investigation; the checkpointer keys saved state by it.
    config = {"configurable": {"thread_id": f"{args.incident_id}-{uuid.uuid4().hex[:6]}"}}
    print(f"investigating {args.incident_id} with {settings.llm_model}")
    request = run_until_pause(graph, {"incident_id": args.incident_id}, config)
    if request is not None:
        answer = ask_human(request, args.decision, args.by)
        run_until_pause(graph, Command(resume=answer), config)

    state = graph.get_state(config).values
    if state.get("stop_reason"):
        print(f"\nstopped: {state['stop_reason']}")
        return
    print("\n=== RESULT =====================================================")
    print(f"root cause:   {state['root_cause']}")
    print(f"confidence:   {state['confidence']:.2f}")
    print(f"rollback:     {state['rollback_assessment']['note']}")
    for b in state["rollback_assessment"]["blockers"]:
        print(f"              blocker: {b}")
    print(f"approval:     {state.get('approval_status')}")
    if state.get("remediation_result"):
        print(f"remediation:  {json.dumps(state['remediation_result'], indent=2)}")
    print(f"knowledge:    {', '.join(d['source'] for d in state.get('rag_evidence', []))}")


if __name__ == "__main__":
    main()
