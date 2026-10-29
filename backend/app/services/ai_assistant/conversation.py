"""Conversation storage for the AI assistant.

Persistent by default (``core.ai_conversations`` / ``core.ai_messages``), with a
small in-process fallback so a temporary database outage does not take the chat
endpoint down. Every lookup is scoped by the authenticated company.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from ...db import execute, fetch_all, fetch_one
from .context import AIContext

VALID_ROLES = {"user", "assistant"}
VALID_CHANNELS = {"web", "whatsapp", "api"}


class ConversationNotFound(Exception):
    pass


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class ConversationStore:
    def __init__(self):
        self._memory: dict[str, dict] = {}
        self._messages: dict[str, list[dict]] = {}
        self._lock = threading.RLock()

    def get_or_create(self, ctx: AIContext, conversation_id: Optional[str],
                      machine_id: Optional[str] = None, channel: str = "web",
                      title: Optional[str] = None) -> dict:
        channel = channel if channel in VALID_CHANNELS else "web"
        if conversation_id:
            if not _valid_uuid(conversation_id):
                raise ConversationNotFound()
            try:
                row = fetch_one(
                    "SELECT conversation_id, company_id, user_id, machine_id, channel, title, "
                    "created_at, updated_at FROM core.ai_conversations "
                    "WHERE conversation_id = %s AND company_id = %s",
                    (conversation_id, ctx.company_id),
                )
            except Exception:
                row = None
            if row:
                if machine_id and str(row.get("machine_id") or "") != str(machine_id):
                    try:
                        execute(
                            "UPDATE core.ai_conversations SET machine_id = %s, updated_at = now() "
                            "WHERE conversation_id = %s AND company_id = %s",
                            (machine_id, conversation_id, ctx.company_id),
                        )
                        row["machine_id"] = machine_id
                    except Exception:
                        pass
                result = dict(row)
                result["conversation_id"] = str(result["conversation_id"])
                return result
            with self._lock:
                memory = self._memory.get(str(conversation_id))
                if memory and str(memory["company_id"]) == ctx.company_id:
                    if machine_id:
                        memory["machine_id"] = machine_id
                    return dict(memory)
            raise ConversationNotFound()

        new_id = str(uuid.uuid4())
        row = {
            "conversation_id": new_id,
            "company_id": ctx.company_id,
            "user_id": ctx.user_id,
            "machine_id": machine_id,
            "channel": channel,
            "title": (title or "")[:200] or None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        try:
            execute(
                "INSERT INTO core.ai_conversations "
                "(conversation_id, company_id, user_id, machine_id, channel, title) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (new_id, ctx.company_id, ctx.user_id, machine_id, channel, row["title"]),
            )
        except Exception:
            with self._lock:
                self._memory[new_id] = dict(row)
        return row

    def history(self, conversation_id: str, limit: int = 20) -> list[dict]:
        try:
            rows = fetch_all(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at, message_id
                    FROM core.ai_messages
                    WHERE conversation_id = %s AND role IN ('user', 'assistant')
                    ORDER BY created_at DESC, message_id DESC
                    LIMIT %s
                ) s ORDER BY created_at, message_id
                """,
                (conversation_id, max(1, int(limit))),
            )
            return [{"role": row["role"], "content": row["content"] or ""} for row in rows]
        except Exception:
            with self._lock:
                return list(self._messages.get(str(conversation_id), []))[-max(1, int(limit)):]

    def append(self, conversation_id: str, role: str, content: str,
               metadata: Optional[dict] = None) -> None:
        if role not in VALID_ROLES:
            return
        content = (content or "")[:12000]
        message = {"role": role, "content": content}
        with self._lock:
            messages = self._messages.setdefault(str(conversation_id), [])
            messages.append(message)
            if len(messages) > 100:
                del messages[:-100]
        try:
            execute(
                "INSERT INTO core.ai_messages (conversation_id, role, content, metadata) "
                "VALUES (%s, %s, %s, %s)",
                (conversation_id, role, content, json.dumps(metadata or {}, default=str)),
            )
            execute("UPDATE core.ai_conversations SET updated_at = now() WHERE conversation_id = %s",
                    (conversation_id,))
        except Exception:
            pass
