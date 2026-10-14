"""Knowledge documents and their embeddings in Redis, with vector search.

Layout: one Redis hash per document, key "knowledge:<source>", holding the
document's text, metadata and embedding (768 float32 values packed as bytes).
A search index (FT.CREATE) over those hashes makes them searchable by vector
similarity (KNN) and filterable by type/service/alert tags.

The commands are sent as raw FT.CREATE / FT.SEARCH so they read exactly like
the Redis documentation and can be retried by hand in redis-cli.
"""

import hashlib
import struct
from dataclasses import dataclass

import redis

from ingestion.documents import Document

INDEX = "idx:knowledge"
PREFIX = "knowledge:"
# Returned by every search. The embedding itself is never sent back.
FIELDS = ("source", "title", "type", "services", "alerts", "tags", "content")


@dataclass(frozen=True)
class SearchResult:
    source: str  # file under knowledge/, e.g. "runbooks/database-errors.md"
    title: str
    type: str
    relevance: float  # 1 - cosine distance: 1.0 = same meaning, ~0 = unrelated
    services: tuple[str, ...]
    alerts: tuple[str, ...]
    tags: tuple[str, ...]
    content: str  # the whole document (no chunking)


def pack_vector(vector: list[float]) -> bytes:
    """float32 little-endian bytes: the format Redis vector fields store."""
    return struct.pack(f"<{len(vector)}f", *vector)


def document_text(doc: Document) -> str:
    """What gets embedded and stored for a document."""
    return f"{doc.title}\n\n{doc.body}"


def content_hash(text: str, model: str) -> str:
    """Changes when the text or the embedding model changes (both need a re-embed)."""
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()[:16]


def _tags(values: tuple[str, ...]) -> str:
    return ",".join(values)


def _escape_tag(value: str) -> str:
    """Tag values in a query must escape punctuation, e.g. incident\\-demo."""
    return "".join(f"\\{c}" if not c.isalnum() and c != "_" else c for c in value)


def _rows(reply) -> list[dict[str, str]]:
    """The returned fields of each FT.SEARCH hit, for both Redis protocol versions.

    RESP3 (redis-py 8's default): {"results": [{"id": key, "extra_attributes": {...}}, ...], ...}
    RESP2: [total, key1, [field, value, ...], key2, [...], ...]
    """
    if isinstance(reply, dict):
        return [hit["extra_attributes"] for hit in reply["results"]]
    return [dict(zip(fields[::2], fields[1::2])) for fields in reply[2::2]]


class RedisKnowledgeStore:
    def __init__(self, client: redis.Redis, dim: int, index: str = INDEX, prefix: str = PREFIX) -> None:
        self.r = client  # created with decode_responses=True
        self.dim = dim
        self.index = index
        self.prefix = prefix

    def ensure_index(self) -> None:
        try:
            self.r.execute_command("FT.INFO", self.index)
            return
        except redis.ResponseError:
            pass  # no index yet
        self.r.execute_command(
            "FT.CREATE", self.index, "ON", "HASH", "PREFIX", 1, self.prefix,
            "SCHEMA",
            "source", "TAG",
            "title", "TEXT",
            "type", "TAG",
            "services", "TAG", "SEPARATOR", ",",
            "alerts", "TAG", "SEPARATOR", ",",
            "tags", "TAG", "SEPARATOR", ",",
            # FLAT = exact search, compares the query with every document.
            # Right for tens of documents; HNSW is the approximate index for millions.
            "embedding", "VECTOR", "FLAT", 6, "TYPE", "FLOAT32", "DIM", self.dim, "DISTANCE_METRIC", "COSINE",
        )

    def drop_index(self) -> None:
        """Delete the index and every document hash it covers (DD)."""
        try:
            self.r.execute_command("FT.DROPINDEX", self.index, "DD")
        except redis.ResponseError:
            pass

    def stored_hashes(self) -> dict[str, str]:
        """source -> content_hash of every stored document."""
        hashes = {}
        for key in self.r.scan_iter(match=f"{self.prefix}*"):
            source = key[len(self.prefix):]
            hashes[source] = self.r.hget(key, "content_hash")
        return hashes

    def upsert(self, doc: Document, vector: list[float], text_hash: str) -> None:
        if len(vector) != self.dim:
            raise ValueError(f"{doc.source}: embedding has {len(vector)} dimensions, index expects {self.dim}")
        self.r.hset(f"{self.prefix}{doc.source}", mapping={
            "source": doc.source,
            "title": doc.title,
            "type": doc.type,
            "services": _tags(doc.services),
            "alerts": _tags(doc.alerts),
            "tags": _tags(doc.tags),
            "content": document_text(doc),
            "content_hash": text_hash,
            "embedding": pack_vector(vector),
        })

    def delete(self, source: str) -> None:
        self.r.delete(f"{self.prefix}{source}")

    def search(self, vector: list[float], k: int = 3, doc_type: str | None = None,
               service: str | None = None) -> list[SearchResult]:
        """The k documents closest in meaning to the query vector, best first."""
        filters = []
        if doc_type:
            filters.append(f"@type:{{{_escape_tag(doc_type)}}}")
        if service:
            # Generic documents apply to every service.
            filters.append(f"@services:{{{_escape_tag(service)} | any}}")
        prefilter = f"({' '.join(filters)})" if filters else "*"
        reply = self.r.execute_command(
            "FT.SEARCH", self.index, f"{prefilter}=>[KNN {k} @embedding $vec AS distance]",
            "PARAMS", 2, "vec", pack_vector(vector),
            "SORTBY", "distance", "ASC",
            "RETURN", len(FIELDS) + 1, *FIELDS, "distance",
            "LIMIT", 0, k,
            "DIALECT", 2,
        )
        results = []
        for f in _rows(reply):
            split = lambda name: tuple(v for v in f.get(name, "").split(",") if v)  # noqa: E731
            results.append(SearchResult(
                source=f["source"], title=f["title"], type=f["type"],
                relevance=round(1 - float(f["distance"]), 4),
                services=split("services"), alerts=split("alerts"), tags=split("tags"),
                content=f["content"],
            ))
        return results
