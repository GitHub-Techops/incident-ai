"""Tests for the Redis store and the indexer, against a real Redis.

Uses its own index and key prefix, removed afterwards, and a deterministic fake
embedder (so no Ollama needed). Skipped when Redis isn't running:
    docker compose -f compose.yaml up -d
"""

import hashlib
import math
import os
import re

import pytest
import redis

from ingestion.documents import Document
from retrieval.indexer import index_documents
from retrieval.redis_store import RedisKnowledgeStore

DIM = 64


class WordEmbedder:
    """Bag of words hashed into 64 dimensions: texts sharing words get close vectors.
    Crude, but deterministic, which is what tests need."""

    model = "test-words"

    def __init__(self):
        self.embedded: list[str] = []

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for word in re.findall(r"[a-z]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts):
        self.embedded += texts
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


def doc(source, title, body, doc_type="runbook", services=("any",)):
    return Document(source=source, title=title, type=doc_type, body=body, services=services)


DOCS = [
    doc("runbooks/db.md", "Database connection errors", "connection refused database port host postgres"),
    doc("runbooks/crash.md", "CrashLoopBackOff", "pod restarts crash exit code oomkilled container"),
    doc("architecture/demo.md", "incident-demo architecture", "orders database endpoints service",
        doc_type="architecture", services=("incident-demo",)),
]


@pytest.fixture
def store():
    client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis is not running")
    s = RedisKnowledgeStore(client, dim=DIM, index="idx:test-knowledge", prefix="test-knowledge:")
    s.drop_index()
    yield s
    s.drop_index()


def test_search_ranks_the_closest_document_first(store):
    embedder = WordEmbedder()
    index_documents(DOCS, embedder, store)

    results = store.search(embedder.embed_query("database connection refused"), k=3)
    assert results[0].source == "runbooks/db.md"
    assert results[0].title == "Database connection errors"
    assert results[0].content.startswith("Database connection errors\n\n")
    assert 0 < results[0].relevance <= 1
    assert [r.relevance for r in results] == sorted((r.relevance for r in results), reverse=True)


def test_filters_by_type_and_service(store):
    embedder = WordEmbedder()
    index_documents(DOCS, embedder, store)
    query = embedder.embed_query("database")

    assert {r.source for r in store.search(query, k=5, doc_type="architecture")} == {"architecture/demo.md"}
    # The service filter keeps generic ("any") documents too.
    assert {r.source for r in store.search(query, k=5, service="incident-demo")} == {
        "runbooks/db.md", "runbooks/crash.md", "architecture/demo.md"}
    assert {r.source for r in store.search(query, k=5, service="payments")} == {
        "runbooks/db.md", "runbooks/crash.md"}


def test_reindex_embeds_only_changes_and_removes_deleted_documents(store):
    embedder = WordEmbedder()
    first = index_documents(DOCS, embedder, store)
    assert sorted(first.added) == sorted(d.source for d in DOCS)

    embedder.embedded.clear()
    changed = [DOCS[0], doc("runbooks/crash.md", "CrashLoopBackOff", "updated text")]  # demo.md deleted
    report = index_documents(changed, embedder, store)

    assert report.unchanged == ["runbooks/db.md"]
    assert report.updated == ["runbooks/crash.md"]
    assert report.removed == ["architecture/demo.md"]
    assert len(embedder.embedded) == 1  # only the changed document was embedded again
    assert set(store.stored_hashes()) == {"runbooks/db.md", "runbooks/crash.md"}


def test_rejects_vectors_of_the_wrong_size(store):
    store.ensure_index()
    with pytest.raises(ValueError, match="dimensions"):
        store.upsert(DOCS[0], [0.1] * (DIM + 1), "h")
