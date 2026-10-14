"""End-to-end retrieval on the real knowledge base: Ollama embeddings + Redis.

The Milestone 12 test from CLAUDE.md: "How do we troubleshoot database
connection failures?" must find the database runbook. Skipped unless both
Redis and Ollama (with nomic-embed-text) are reachable.
"""

import os

import pytest
import redis

from embeddings.ollama import EmbeddingError, OllamaEmbedder
from ingestion.documents import load_documents
from retrieval.indexer import index_documents
from retrieval.redis_store import RedisKnowledgeStore


@pytest.fixture(scope="module")
def live():
    client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    embedder = OllamaEmbedder(os.getenv("OLLAMA_URL", "http://localhost:11434"))
    try:
        client.ping()
        embedder.embed_query("ping")
    except (redis.ConnectionError, EmbeddingError) as exc:
        pytest.skip(f"needs Redis and Ollama: {exc}")
    store = RedisKnowledgeStore(client, dim=768, index="idx:test-live", prefix="test-live:")
    store.drop_index()
    index_documents(load_documents(), embedder, store)
    yield store, embedder
    store.drop_index()


@pytest.mark.parametrize("question, expected", [
    ("How do we troubleshoot database connection failures?", "runbooks/database-errors.md"),
    ("A pod keeps restarting and gets OOMKilled", "runbooks/crashloopbackoff.md"),
    ("How do I roll back a bad release?", "runbooks/deployment-failure.md"),
    ("Which database does the orders service use?", "architecture/incident-demo.md"),
])
def test_question_finds_the_right_document(live, question, expected):
    store, embedder = live
    results = store.search(embedder.embed_query(question), k=3)
    assert expected in [r.source for r in results], [(r.source, r.relevance) for r in results]
