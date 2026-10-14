"""Root-cause analysis: evidence + knowledge -> validated RootCauseAnalysis.

    python -m chains.rca --fixture                 # the recorded v2 incident (no cluster needed)
    python -m chains.rca --incident INC-...        # a live incident from the backend
    python -m chains.rca --fixture --reasoning     # let the model think first

This is a fixed pipeline, not an agent: collect all evidence, retrieve
knowledge, ask once. Milestone 15 turns it into a LangGraph workflow.

Structured output: LangChain sends RootCauseAnalysis as a JSON schema (Ollama
constrains the reply to it) and parses the reply with Pydantic. Ollama enforces
the shape but not every rule (e.g. confidence <= 1), so a reply can still fail
validation; it is then sent back with the error, once.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.retrievers import BaseRetriever

from llm import make_chat_model
from prompts.rca import CORRECTION_PROMPT, RCA_PROMPT
from schemas import RootCauseAnalysis
from settings import Settings
from tools.backend import BackendClient
from tools.evidence_format import format_alert, format_deployment, format_kubernetes, format_metrics
from tools.knowledge import format_documents, make_retriever

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "v2-incident-evidence.json"


class RCAError(RuntimeError):
    pass


def analyze(llm: BaseChatModel, alert: str, evidence: str, knowledge: str, max_attempts: int = 2,
            previous: RootCauseAnalysis | None = None, problems: list[str] | None = None,
            ) -> tuple[RootCauseAnalysis, int]:
    """Returns the validated analysis and how many attempts it took.

    With `previous` and `problems`, it is a correction round: the model sees its
    earlier answer and the statements the verifier could not find in the evidence.
    """
    structured = llm.with_structured_output(RootCauseAnalysis, include_raw=True)
    messages: list[BaseMessage] = RCA_PROMPT.format_messages(alert=alert, evidence=evidence, knowledge=knowledge)
    if previous is not None and problems:
        messages += [AIMessage(previous.model_dump_json()),
                     HumanMessage(CORRECTION_PROMPT.format(problems="\n".join(f"- {p}" for p in problems)))]
    for attempt in range(1, max_attempts + 1):
        out = structured.invoke(messages)
        if out["parsing_error"] is None and out["parsed"] is not None:
            return out["parsed"], attempt
        error = out["parsing_error"]
        messages = messages + [out["raw"], HumanMessage(
            f"Your answer does not match the required format: {error}\n"
            "Return the corrected JSON object only.")]
    raise RCAError(f"no valid analysis after {max_attempts} attempts; last error: {error}")


def most_telling_error(lines: list[str]) -> str | None:
    """The most frequent log line that names an actual error.

    Lines like {"msg": "request", "status": 500} only say a request failed;
    {"error": "database connection refused"} says why. In the M14 live run the
    first error line was the former, and the knowledge search used "request".
    """
    counts: Counter[str] = Counter()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            counts[line] += 1  # plain-text log line: keep as is
            continue
        if isinstance(entry, dict) and entry.get("error"):
            counts[line] += 1
    return counts.most_common(1)[0][0] if counts else None


def retrieval_queries(alert_summary: str, error_line: str | None) -> list[str]:
    """One knowledge search per signal: the alert, and the most telling error.

    Measured on the v2 incident: one combined query ("High HTTP 5xx error rate
    ... database connection refused") ranked the database runbook 4th, while
    each signal on its own ranks its runbook 1st. Mixing topics blurs the query.
    """
    queries = [alert_summary] if alert_summary else []
    if error_line:
        try:
            entry = json.loads(error_line)
            error_line = str(entry.get("error") or entry.get("msg") or error_line)
        except (ValueError, AttributeError):
            pass
        queries.append(error_line)
    return queries


def retrieve(retriever: BaseRetriever, queries: list[str], per_query: int = 2) -> list[Document]:
    """Top documents for each query, merged without duplicates, best first."""
    best: dict[str, Document] = {}
    for query in queries:
        for doc in retriever.invoke(query)[:per_query]:
            source = doc.metadata["source"]
            if source not in best or doc.metadata["relevance"] > best[source].metadata["relevance"]:
                best[source] = doc
    return sorted(best.values(), key=lambda d: d.metadata["relevance"], reverse=True)


def from_fixture() -> tuple[str, str, list[str]]:
    """alert, evidence and knowledge queries from the recorded v2 incident."""
    data = json.loads(FIXTURE.read_text())
    data.pop("_source")
    alert = data.pop("alert")
    queries = retrieval_queries(alert["summary"], data["kubernetes"]["sample_error_log"])
    return json.dumps(alert, indent=2), json.dumps(data, indent=2), queries


def from_backend(settings: Settings, incident_id: str) -> tuple[str, str, list[str]]:
    client = BackendClient(settings.backend_url)
    incident = client.get_incident(incident_id)
    k8s = client.collect_evidence(incident_id, "kubernetes")
    metrics = client.collect_evidence(incident_id, "metrics")
    deployment = client.collect_evidence(incident_id, "deployment")
    evidence = "\n\n".join([format_kubernetes(k8s), format_metrics(metrics), format_deployment(deployment)])
    error = most_telling_error([line for logs in k8s.get("logs", []) for line in logs.get("error_lines", [])])
    return format_alert(incident), evidence, retrieval_queries(incident.get("summary") or "", error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", action="store_true", help="use the recorded v2 incident")
    source.add_argument("--incident", metavar="ID", help="a live incident, e.g. INC-20261014-1a2b3c")
    parser.add_argument("--reasoning", action="store_true", help="let the model think before answering")
    parser.add_argument("--no-knowledge", action="store_true", help="skip the knowledge base (baseline)")
    args = parser.parse_args()

    settings = Settings.from_env()
    alert, evidence, queries = from_fixture() if args.fixture else from_backend(settings, args.incident)

    knowledge = "(none)"
    if not args.no_knowledge:
        docs = retrieve(make_retriever(settings, service="incident-demo"), queries)
        print(f"knowledge queries: {queries}")
        for d in docs:
            print(f"  {d.metadata['relevance']:.3f}  {d.metadata['source']}")
        knowledge = format_documents(docs)

    llm = make_chat_model(settings, reasoning=args.reasoning)
    start = time.perf_counter()
    try:
        result, attempts = analyze(llm, alert, evidence, knowledge)
    except RCAError as exc:
        sys.exit(str(exc))
    print(f"\n{settings.llm_model}, reasoning {'on' if args.reasoning else 'off'}: "
          f"{time.perf_counter() - start:.1f} s, {attempts} attempt(s)\n")
    print(result.model_dump_json(indent=2))

    if args.fixture:  # known root cause of the recorded incident; never shown to the model
        text = f"{result.root_cause} {' '.join(result.inferences)}".lower()
        print("\nground-truth checks:")
        print(f"  names port 5433       {'yes' if '5433' in text else 'NO'}")
        print(f"  recommends rollback   {'yes' if result.rollback_recommended else 'NO'}")
        print(f"  confidence < 1.0      {'yes' if result.confidence < 1 else 'NO'} ({result.confidence:.2f})")


if __name__ == "__main__":
    main()
