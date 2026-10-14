"""Agent configuration, read from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # From WSL: http://<windows host>:11434, see CHECKLIST.md.
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "gemma4:12b"
    # Context window per request. Ollama's server default on this machine is
    # 262144 tokens, far more than fits in VRAM; evidence + runbooks + answer
    # fit in 12288.
    num_ctx: int = 12288
    # Let the model reason before answering (slower, often more careful).
    reasoning: bool = False
    # The incident backend; from WSL: kubectl port-forward -n incident-ai svc/incident-backend 8080:80
    backend_url: str = "http://localhost:8080"
    redis_url: str = "redis://localhost:6379/0"
    embed_model: str = "nomic-embed-text"
    knowledge_top_k: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            ollama_url=os.getenv("OLLAMA_URL", d.ollama_url),
            llm_model=os.getenv("LLM_MODEL", d.llm_model),
            num_ctx=int(os.getenv("LLM_NUM_CTX", d.num_ctx)),
            reasoning=os.getenv("LLM_REASONING", str(d.reasoning)).lower() == "true",
            backend_url=os.getenv("BACKEND_URL", d.backend_url),
            redis_url=os.getenv("REDIS_URL", d.redis_url),
            embed_model=os.getenv("EMBED_MODEL", d.embed_model),
            knowledge_top_k=int(os.getenv("KNOWLEDGE_TOP_K", d.knowledge_top_k)),
        )
