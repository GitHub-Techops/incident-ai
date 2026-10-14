"""The RCA chain's prompt, validation and retry, with a scripted fake model."""

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.retrievers import BaseRetriever
from pydantic import ValidationError

from chains.rca import RCAError, analyze, most_telling_error, retrieval_queries, retrieve
from schemas import Fact, RootCauseAnalysis

VALID = RootCauseAnalysis(
    summary="Orders fail.",
    facts=[Fact(statement="connection refused to orders-db:5433", source="kubernetes",
                quote='"db_host": "orders-db:5433"')],
    inferences=["v2 default port is wrong"], root_cause="DB_PORT defaults to 5433", confidence=0.85,
    recommended_actions=["roll back"], rollback_recommended=True, rollback_reason="v2 introduced it",
    knowledge_sources=["runbooks/database-errors.md"],
)


def validation_error() -> ValidationError:
    try:
        RootCauseAnalysis.model_validate({**VALID.model_dump(), "confidence": 5})
    except ValidationError as exc:
        return exc


class ScriptedModel:
    """Stands in for ChatOllama: returns pre-recorded structured outputs in order."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.seen: list[list] = []

    def with_structured_output(self, schema, include_raw=False):
        assert schema is RootCauseAnalysis and include_raw
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        return self.outputs.pop(0)


def ok():
    return {"raw": AIMessage("{...}"), "parsed": VALID, "parsing_error": None}


def bad():
    return {"raw": AIMessage('{"confidence": 5}'), "parsed": None, "parsing_error": validation_error()}


def test_knowledge_sources_is_required_in_the_schema_sent_to_the_model():
    assert "knowledge_sources" in RootCauseAnalysis.model_json_schema()["required"]


def test_prompt_contains_evidence_verbatim_and_knowledge_sources():
    model = ScriptedModel([ok()])
    evidence = 'log: {"error": "database connection refused", "db_host": "orders-db:5433"}'
    result, attempts = analyze(model, "HighErrorRate", evidence, "### Source: runbooks/database-errors.md\n...")
    assert (result, attempts) == (VALID, 1)
    system, human = model.seen[0]
    assert "Never invent" in system.content and "line number" in system.content
    assert evidence in human.content  # braces in the evidence are not template variables
    assert "### Source: runbooks/database-errors.md" in human.content
    assert "never 1.0" in human.content


def test_invalid_answer_is_sent_back_with_the_error_once():
    model = ScriptedModel([bad(), ok()])
    result, attempts = analyze(model, "a", "e", "k")
    assert (result, attempts) == (VALID, 2)
    retry = model.seen[1]
    assert retry[-2].content == '{"confidence": 5}'  # the model sees its own invalid answer
    assert "less than or equal to 1" in retry[-1].content  # and why it was rejected


def test_correction_round_shows_the_previous_answer_and_the_problems():
    model = ScriptedModel([ok()])
    analyze(model, "a", "e", "k", previous=VALID, problems=["fact \"x\": 'line 18' does not appear in the evidence"])
    *_, previous_answer, request = model.seen[0]
    assert "orders-db:5433" in previous_answer.content  # its own earlier JSON
    assert "'line 18' does not appear in the evidence" in request.content


def test_gives_up_after_two_invalid_answers():
    with pytest.raises(RCAError, match="no valid analysis after 2 attempts"):
        analyze(ScriptedModel([bad(), bad()]), "a", "e", "k")


def test_one_query_per_signal_using_the_error_not_the_raw_json():
    line = '{"level": "ERROR", "msg": "order failed", "error": "database connection refused", "db_host": "x"}'
    assert retrieval_queries("High HTTP 5xx error rate", line) == \
        ["High HTTP 5xx error rate", "database connection refused"]
    assert retrieval_queries("High HTTP 5xx error rate", None) == ["High HTTP 5xx error rate"]
    assert retrieval_queries("x", "plain text error") == ["x", "plain text error"]


def test_most_telling_error_skips_lines_without_a_cause():
    generic = '{"level": "ERROR", "msg": "request", "path": "/api/order", "status": 500}'
    cause = '{"level": "ERROR", "msg": "order failed", "error": "database connection refused"}'
    assert most_telling_error([generic, generic, generic, cause, cause]) == cause
    assert most_telling_error([generic]) is None
    assert most_telling_error(["Traceback: boom"]) == "Traceback: boom"


class TableRetriever(BaseRetriever):
    """Fixed results per query: {query: [(source, relevance), ...]}."""

    table: dict

    def _get_relevant_documents(self, query, *, run_manager):
        return [Document(page_content=s, metadata={"source": s, "relevance": r}) for s, r in self.table[query]]


def test_retrieve_merges_queries_without_duplicates_best_first():
    retriever = TableRetriever(table={
        "alert": [("runbooks/high-error-rate.md", 0.78), ("architecture/demo.md", 0.71), ("x.md", 0.70)],
        "error": [("runbooks/database-errors.md", 0.79), ("architecture/demo.md", 0.75)],
    })
    docs = retrieve(retriever, ["alert", "error"], per_query=2)
    assert [(d.metadata["source"], d.metadata["relevance"]) for d in docs] == [
        ("runbooks/database-errors.md", 0.79), ("runbooks/high-error-rate.md", 0.78),
        ("architecture/demo.md", 0.75),  # found by both: best relevance kept, listed once
    ]
