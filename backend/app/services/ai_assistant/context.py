"""Authenticated, server-derived context for the AI assistant.

The customer identity and permissions are never accepted from the chat request
body. They come from the existing Bearer-token dependency (``get_current_user``)
and are re-read from the database on every request.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...deps import require_company


@dataclass(frozen=True)
class AIContext:
    company_id: str
    user_id: str
    username: str
    role: Optional[str] = None
    permissions: tuple[str, ...] = field(default_factory=tuple)

    def as_public(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
        }


def build_context(user: dict) -> AIContext:
    """Build a tenant-bound context from the authenticated user row.

    ``require_company`` rejects global vendor accounts, so every AI query is
    automatically scoped to exactly one customer/company.
    """
    company_id = require_company(user)
    role = user.get("role")
    permissions = ("ai.chat",)
    if role:
        permissions = permissions + (f"role:{role}",)
    return AIContext(
        company_id=company_id,
        user_id=str(user["id"]),
        username=str(user.get("username") or ""),
        role=role,
        permissions=permissions,
    )
