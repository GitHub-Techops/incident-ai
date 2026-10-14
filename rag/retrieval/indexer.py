"""Keep the Redis index in sync with knowledge/: embed new or changed documents,
remove deleted ones, skip the rest."""

from dataclasses import dataclass, field
from typing import Protocol

from ingestion.documents import Document
from retrieval.redis_store import RedisKnowledgeStore, content_hash, document_text


class Embedder(Protocol):
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass
class IndexReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def index_documents(docs: list[Document], embedder: Embedder, store: RedisKnowledgeStore,
                    force: bool = False) -> IndexReport:
    store.ensure_index()
    report = IndexReport()
    stored = store.stored_hashes()

    todo = []
    for doc in docs:
        text_hash = content_hash(document_text(doc), embedder.model)
        if not force and stored.get(doc.source) == text_hash:
            report.unchanged.append(doc.source)
        else:
            todo.append((doc, text_hash))

    if todo:
        vectors = embedder.embed_documents([document_text(doc) for doc, _ in todo])
        for (doc, text_hash), vector in zip(todo, vectors):
            (report.updated if doc.source in stored else report.added).append(doc.source)
            store.upsert(doc, vector, text_hash)

    current = {doc.source for doc in docs}
    for source in sorted(set(stored) - current):
        store.delete(source)
        report.removed.append(source)
    return report
