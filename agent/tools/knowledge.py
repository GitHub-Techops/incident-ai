"""The Redis knowledge search (Milestone 12) as a LangChain retriever.

A retriever is LangChain's standard interface for "give me documents relevant
to this text". Wrapping our search in it means chains and tools can use it
like any other LangChain retriever, and every result keeps its source file
and relevance score in the metadata.
"""

from typing import Any

import redis
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from embeddings.ollama import OllamaEmbedder
from retrieval.redis_store import RedisKnowledgeStore
from settings import Settings

EMBED_DIM = 768  # nomic-embed-text


class KnowledgeRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: Any  # RedisKnowledgeStore
    embedder: Any  # OllamaEmbedder (anything with embed_query)
    k: int = 3
    service: str | None = None  # only documents for this service, plus generic ones

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        hits = self.store.search(self.embedder.embed_query(query), k=self.k, service=self.service)
        return [
            Document(page_content=hit.content, metadata={
                "source": hit.source, "title": hit.title, "type": hit.type, "relevance": hit.relevance,
            })
            for hit in hits
        ]


def make_retriever(settings: Settings, service: str | None = None) -> KnowledgeRetriever:
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return KnowledgeRetriever(
        store=RedisKnowledgeStore(client, dim=EMBED_DIM),
        embedder=OllamaEmbedder(settings.ollama_url, settings.embed_model),
        k=settings.knowledge_top_k, service=service,
    )


def format_documents(docs: list[Document]) -> str:
    """Knowledge documents for the prompt, each headed by its source so the model can cite it."""
    return "\n\n".join(
        f"### Source: {d.metadata['source']} (relevance {d.metadata['relevance']:.2f})\n{d.page_content}"
        for d in docs
    )
