"""Investigate an incident with the LangGraph workflow.

    python investigate.py INC-20261014-1a2b3c                   # asks you to approve/reject
    python investigate.py INC-... --decision reject --by alice  # non-interactive
    python investigate.py --graph                               # print the graph (Mermaid)

Needs the backend (kubectl port-forward -n incident-ai svc/incident-backend 8080:80),
Redis and Ollama (OLLAMA_URL).
"""

import argparse
import sys
import uuid
from pathlib import Path

from langgraph.types import Command

from graph.incident_graph import build_graph
from llm import make_chat_model
from nodes.incident_nodes import Deps
from report.schema import IncidentReport
from settings import Settings
from tools.backend import BackendClient
from tools.knowledge import make_retriever

REPORTS = Path(__file__).parent / "reports"  # local output, not committed


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
    print(f"Facts:     {request['facts_verified']} verified against the evidence"
          + (f"; confidence {request['confidence_note']}" if request.get("confidence_note") else ""))
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
                llm=make_chat_model(settings, reasoning=args.reasoning), model_name=settings.llm_model)
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
    report = IncidentReport.model_validate(state["report"])
    print_report(report)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{report.incident_id}.json"
    path.write_text(report.model_dump_json(indent=2) + "\n")
    print(f"\nreport saved to {path}")


def print_report(r: IncidentReport) -> None:
    v = r.verification
    print(f"\n=== INCIDENT REPORT {r.incident_id} " + "=" * 30)
    print(f"{r.severity} | {r.namespace}/{r.service} | status {r.status} | model {r.model}")
    print(f"\nSummary:    {r.summary}")
    print(f"Root cause: {r.root_cause}")
    print(f"Confidence: {r.confidence:.2f}" + (f"  ({v.confidence_note})" if v.confidence_note else ""))
    print(f"\nFACTS ({v.facts_verified}/{v.facts_total} verified against the evidence"
          + (", after one correction round" if v.corrected else "") + "):")
    for e in r.evidence:
        if e.kind == "fact":
            print(f"  {'✓' if e.verified else '✗'} [{e.source}] {e.statement}")
            for issue in e.issues:
                print(f"      ! {issue}")
    print("\nINFERENCES:")
    for e in r.evidence:
        if e.kind == "inference":
            print(f"  - {e.statement}")
    for issue in v.root_cause_issues:
        print(f"  ! root cause: {issue}")
    for issue in v.statement_issues:
        print(f"  ! {issue}")
    print("\nRecommended actions:")
    for action in r.recommended_actions:
        print(f"  - {action}")
    print(f"\nRollback:   {'yes' if r.rollback_recommended else 'no'}. {r.rollback_reason}")
    if r.remediation:
        print(f"Remediation: {r.remediation['proposal']['summary']} -> {r.remediation['approval_status']}"
              + (f", {r.remediation['result']['status']}" if r.remediation.get("result") else ""))
    print(f"Knowledge:  {', '.join(r.knowledge_sources) or '(none)'}")


if __name__ == "__main__":
    main()
