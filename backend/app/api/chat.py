"""Incident chat. Placeholder until the LLM is wired in (Milestone 14)."""

from fastapi import APIRouter, HTTPException

from app.models.incident import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(body: ChatRequest) -> None:
    raise HTTPException(status_code=501, detail="Chat is not implemented yet (Milestone 14: LangChain + Ollama).")
