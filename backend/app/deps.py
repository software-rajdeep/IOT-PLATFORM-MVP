"""FastAPI dependencies — authentication and tenant context."""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status

from .db import fetch_one
from .security import decode_token


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """Resolve the logged-in user from a Bearer token.

    The token carries the user id; the company is always re-read from the DB so a
    revoked/reassigned user cannot keep stale tenant access.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    if not payload or not payload.get("uid"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = fetch_one(
        """
        SELECT u.id, u.company_id, u.username, u.email, u.status,
               r.role_name AS role
        FROM core.users u
        LEFT JOIN core.roles r ON r.role_id = u.role_id
        WHERE u.id = %s
        """,
        (payload["uid"],),
    )
    if not user or user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not active")
    return user


def require_company(user: dict) -> str:
    """Return the user's company_id or 403 if they are a global vendor account.

    Tenant isolation rule: every company-scoped query filters on this value.
    """
    if not user.get("company_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires a company-scoped account",
        )
    return str(user["company_id"])
