"""The local chat model, through LangChain's Ollama integration."""

from langchain_ollama import ChatOllama

from settings import Settings


def make_chat_model(settings: Settings, reasoning: bool | None = None) -> ChatOllama:
    return ChatOllama(
        base_url=settings.ollama_url,
        model=settings.llm_model,
        # 0 = the same evidence gives the same answer (useful to compare runs).
        temperature=0,
        num_ctx=settings.num_ctx,
        # True: gemma4 thinks before answering; the thinking is returned
        # separately (additional_kwargs["reasoning_content"]), not mixed into the answer.
        reasoning=settings.reasoning if reasoning is None else reasoning,
    )
