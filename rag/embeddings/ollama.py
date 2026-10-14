"""Local embeddings through Ollama (nomic-embed-text).

An embedding turns a text into a list of numbers (768 for this model) such that
texts with similar meaning get similar numbers. Search then means: embed the
question, find the stored documents whose numbers are closest.

nomic-embed-text was trained with task prefixes and works best with them:
"search_document: " for what we store, "search_query: " for questions.
Documents are embedded whole (no chunking). Ollama runs this model with a
2048-token context; our documents measure 527-898 tokens (2026-10-14). A
document that grows past the limit makes indexing fail (truncate=False below),
which is the signal to split it or bring chunking back.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
CONTEXT_TOKENS = 2048  # nomic-embed-text's context length in Ollama (nomic-bert.context_length)

PostFn = Callable[[str, dict, float], dict]


class EmbeddingError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise EmbeddingError(f"Ollama returned HTTP {exc.code}: {exc.read().decode()[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise EmbeddingError(f"cannot reach Ollama at {url}: {exc} (is Ollama running?)") from exc


class OllamaEmbedder:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text",
                 timeout: float = 120.0, post: PostFn = _post_json) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._post = post  # replaced in tests

    def _embed(self, texts: list[str]) -> list[list[float]]:
        reply = self._post(f"{self.base_url}/api/embed", {
            "model": self.model,
            "input": texts,
            # Fail instead of silently cutting a long document: a truncated
            # document would be searchable by its beginning only.
            "truncate": False,
            "options": {"num_ctx": CONTEXT_TOKENS},
        }, self.timeout)
        vectors = reply.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError(f"unexpected reply from Ollama: {str(reply)[:300]}")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([DOCUMENT_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_PREFIX + text])[0]
