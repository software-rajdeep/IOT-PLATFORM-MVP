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
    customer_demo_db: str = os.environ.get(
        "CUSTOMER_DEMO_DATABASE_URL",
        "postgresql://postgres:linux@localhost:5432/ucap_customer_corp",
    )
    auth_secret: str = os.environ.get("AUTH_SECRET", "dev-secret-change-me")
    token_ttl_seconds: int = int(os.environ.get("TOKEN_TTL_SECONDS", "604800"))
    app_host: str = os.environ.get("APP_HOST", "127.0.0.1")
    app_port: int = int(os.environ.get("APP_PORT", "8000"))
    enable_simulator: bool = _bool("ENABLE_SIMULATOR", True)
    # Source runtime: polls every active device/source (real and simulated).
    enable_poller: bool = _bool("ENABLE_POLLER", True)
    # Demo customer system writes a live reading every second into its own DB.
    enable_demo_customer: bool = _bool("ENABLE_DEMO_CUSTOMER", True)
    # Allow customer sources on private/loopback addresses (dev, LAN gateways).
    allow_private_sources: bool = _bool("ALLOW_PRIVATE_SOURCES", True)
    public_base_url: str = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    # Gmail OAuth email alerts. Credentials come from the environment; tokens and
    # per-company settings are written to EMAIL_ALERT_DATA_DIR.
    email_data_dir: str = os.environ.get("EMAIL_ALERT_DATA_DIR", str(BACKEND_DIR / "data" / "email_alerts"))
    google_oauth_client_id: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    google_oauth_client_secret: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    google_oauth_redirect_uri: str = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        + "/api/v1/email-alerts/oauth-callback",
    )
    # Seconds a built dashboard is reused to coalesce bursts of live refreshes.
    # Set to 0 to disable. Cleared immediately on any write request.
    dashboard_cache_ttl: float = float(os.environ.get("DASHBOARD_CACHE_TTL", "1.0"))

    # Rajdeep AI Assistant. Read-only in V1. The deterministic assistant works
    # without an external model; set AI_PROVIDER and AI_API_KEY to enable the
    # tool-calling orchestrator. Keys are read only on the server.
    ai_enabled: bool = _bool("AI_ENABLED", True)
    ai_provider: str = os.environ.get("AI_PROVIDER", "openai").strip().lower()
    ai_model: str = os.environ.get("AI_MODEL", "gpt-4o-mini")
    ai_api_key: str = os.environ.get("AI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    ai_base_url: str = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_timeout: float = float(os.environ.get("AI_TIMEOUT", "30"))
    ai_timeout_seconds: float = float(os.environ.get("AI_TIMEOUT_SECONDS", str(ai_timeout)))
    ai_max_history: int = int(os.environ.get("AI_MAX_HISTORY", "12"))
    ai_max_history_messages: int = int(
        os.environ.get("AI_MAX_HISTORY_MESSAGES", str(ai_max_history)))
    ai_retention_days: int = int(os.environ.get("AI_RETENTION_DAYS", "90"))
    ai_worker_enabled: bool = _bool("AI_WORKER_ENABLED", True)
    ai_max_output_tokens: int = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "800"))
    ai_max_tool_calls: int = int(os.environ.get("AI_MAX_TOOL_CALLS", "6"))
    ai_max_tool_rounds: int = int(os.environ.get("AI_MAX_TOOL_ROUNDS", "3"))
    ai_max_message_chars: int = int(os.environ.get("AI_MAX_MESSAGE_CHARS", "4000"))
    ai_max_response_chars: int = int(os.environ.get("AI_MAX_RESPONSE_CHARS", "6000"))
    ai_max_range_days: int = int(os.environ.get("AI_MAX_RANGE_DAYS", "31"))
    ai_max_tool_rows: int = int(os.environ.get("AI_MAX_TOOL_ROWS", "200"))
    ai_default_timezone: str = os.environ.get("AI_DEFAULT_TIMEZONE", "Asia/Kolkata")

    # WhatsApp Cloud API. While no Meta developer account exists, use
    # WHATSAPP_DRIVER=mock: messages are logged and queued, never sent.
    whatsapp_driver: str = os.environ.get("WHATSAPP_DRIVER", "mock").strip().lower()
    whatsapp_verify_token: str = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_app_secret: str = os.environ.get("WHATSAPP_APP_SECRET", "")
    whatsapp_access_token: str = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_graph_version: str = os.environ.get("WHATSAPP_GRAPH_VERSION", "v20.0")
    # Local testing convenience only. Never enable with a public webhook.
    whatsapp_allow_unsigned: bool = _bool("WHATSAPP_ALLOW_UNSIGNED", False)


settings = Settings()
