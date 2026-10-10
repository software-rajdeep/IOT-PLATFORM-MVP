"""Password hashing (bcrypt) and signed login tokens (itsdangerous)."""
from __future__ import annotations

from typing import Any, Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.auth_secret, salt="ucap-auth")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(payload: dict[str, Any]) -> str:
    return _serializer.dumps(payload)


def decode_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return _serializer.loads(token, max_age=settings.token_ttl_seconds)
    except (BadSignature, SignatureExpired):
        return None
