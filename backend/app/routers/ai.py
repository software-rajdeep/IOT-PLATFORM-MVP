"""AI Assistant HTTP adapter.

The router deliberately contains no analytics logic: it authenticates the
request with the platform's existing Bearer token, derives the tenant context
server-side, and delegates to the channel-independent ChatService.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..deps import get_current_user
from ..services.ai_assistant import build_context, get_chat_service

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    machine_id: Optional[str] = Field(default=None, max_length=128)
    conversation_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/chat")
def chat(body: ChatRequest, user: dict = Depends(get_current_user)) -> dict:
    context = build_context(user)
    return get_chat_service().chat(
        context,
        body.message,
        machine_id=body.machine_id,
        conversation_id=body.conversation_id,
        channel="web",
    )
