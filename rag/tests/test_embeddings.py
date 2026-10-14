"""Tests for the Ollama embedder, without Ollama: the HTTP call is faked."""

import pytest

from embeddings.ollama import EmbeddingError, OllamaEmbedder


class FakePost:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload))
        return self.reply if self.reply is not None else {"embeddings": [[0.1, 0.2]] * len(payload["input"])}


def test_documents_and_queries_get_their_task_prefixes():
    post = FakePost()
    embedder = OllamaEmbedder("http://ollama:11434/", post=post)
    embedder.embed_documents(["doc a", "doc b"])
    embedder.embed_query("why?")

    (url, docs), (_, query) = post.calls
    assert url == "http://ollama:11434/api/embed"
    assert docs["model"] == "nomic-embed-text"
    assert docs["input"] == ["search_document: doc a", "search_document: doc b"]
    assert query["input"] == ["search_query: why?"]
    assert docs["truncate"] is False  # long documents fail loudly instead of being cut


def test_unexpected_reply_is_an_error():
    embedder = OllamaEmbedder(post=FakePost(reply={"error": "model not found"}))
    with pytest.raises(EmbeddingError, match="unexpected reply"):
        embedder.embed_query("x")
