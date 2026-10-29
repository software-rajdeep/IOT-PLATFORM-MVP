"""Rajdeep AI Assistant (Phase 4).

Channel-independent chat service: the web dashboard and future WhatsApp/API
channels call the same orchestrator, analytics tools and conversation store.
"""
from .context import AIContext, build_context
from .service import ChatService, get_chat_service

__all__ = ["AIContext", "build_context", "ChatService", "get_chat_service"]
