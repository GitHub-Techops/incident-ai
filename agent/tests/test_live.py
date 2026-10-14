"""The RCA chain against the real model and knowledge base, on the recorded v2
incident. Skipped unless Ollama and Redis are reachable (set OLLAMA_URL)."""

import pytest

from chains.rca import analyze, from_fixture, retrieve
from embeddings.ollama import EmbeddingError
from llm import make_chat_model
from settings import Settings
from tools.knowledge import format_documents, make_retriever


@pytest.fixture(scope="module")
def settings():
    s = Settings.from_env()
    try:
        make_retriever(s).invoke("ping")
    except (EmbeddingError, Exception) as exc:  # noqa: BLE001 - any connection problem means skip
        pytest.skip(f"needs Ollama and Redis: {exc}")
    return s


def test_v2_incident_root_cause_with_knowledge(settings):
    alert, evidence, queries = from_fixture()
    docs = retrieve(make_retriever(settings, service="incident-demo"), queries)
    sources = [d.metadata["source"] for d in docs]
    assert "runbooks/database-errors.md" in sources and "runbooks/high-error-rate.md" in sources

    result, _ = analyze(make_chat_model(settings), alert, evidence, format_documents(docs))
    assert "5433" in f"{result.root_cause} {' '.join(result.inferences)}"
    assert result.rollback_recommended
    assert 0 < result.confidence < 1
