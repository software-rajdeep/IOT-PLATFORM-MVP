"""Application configuration, loaded from environment (and a local .env if present).

No third-party dependency (python-dotenv may not be available offline), so a tiny
.env parser is included.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from backend/.env into os.environ (non-destructive)."""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # .env must not override an explicitly-set real environment variable.
        os.environ.setdefault(key, value)


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:linux@localhost:5432/ucap_platform"
    )
    auth_secret: str = os.environ.get("AUTH_SECRET", "dev-secret-change-me")
    token_ttl_seconds: int = int(os.environ.get("TOKEN_TTL_SECONDS", "604800"))
    app_host: str = os.environ.get("APP_HOST", "127.0.0.1")
    app_port: int = int(os.environ.get("APP_PORT", "8000"))
    enable_simulator: bool = _bool("ENABLE_SIMULATOR", True)


settings = Settings()
