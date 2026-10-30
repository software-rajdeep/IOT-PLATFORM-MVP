"""Gmail OAuth email delivery for alert notifications.

Settings and tokens are stored in the platform database
(``core.email_alert_settings``) so they survive across serverless invocations.
Pending OAuth state, including the PKCE verifier, lives in
``core.email_oauth_states`` so the callback can be handled by any instance.
Google client credentials come from the environment, never from source code.
"""
from __future__ import annotations

import base64
import copy
import html
import json
import os
import secrets
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from psycopg2.extras import Json

from ..config import settings
from ..db import execute, fetch_one

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    GOOGLE_API_AVAILABLE = True
except ImportError:
    Credentials = None
    InstalledAppFlow = None
    GoogleRequest = None
    build = None
    GOOGLE_API_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

ALERT_LABELS = {
    "fail_streak": "Consecutive FAIL readings",
    "offline": "Source offline",
    "mapping_failure": "Data cannot be mapped",
}

DEFAULT_CONFIG = {
    "enabled": False,
    "api_type": "gmail_oauth",
    "recipient_email": "",
    "alerts": {"fail_streak": True, "offline": True, "mapping_failure": True},
    "cooldown": {"enabled": True, "minutes": 5},
}

OAUTH_STATE_TTL_MINUTES = 15

_lock = threading.RLock()
_last_alert_times: dict[tuple[str, str, str], float] = {}


# --------------------------------------------------------------------------- storage
def _read_config(company_id: str) -> Optional[dict]:
    row = fetch_one(
        "SELECT config FROM core.email_alert_settings WHERE company_id = %s",
        (str(company_id),),
    )
    value = (row or {}).get("config")
    return value if isinstance(value, dict) else None


def _write_config(company_id: str, config: dict) -> None:
    execute(
        """
        INSERT INTO core.email_alert_settings (company_id, config, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (company_id)
        DO UPDATE SET config = EXCLUDED.config, updated_at = now()
        """,
        (str(company_id), Json(config)),
    )


def _read_token(company_id: str) -> Optional[dict]:
    row = fetch_one(
        "SELECT token FROM core.email_alert_settings WHERE company_id = %s",
        (str(company_id),),
    )
    value = (row or {}).get("token")
    return value if isinstance(value, dict) else None


def _write_token(company_id: str, token: Optional[dict]) -> None:
    execute(
        """
        INSERT INTO core.email_alert_settings (company_id, token, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (company_id)
        DO UPDATE SET token = EXCLUDED.token, updated_at = now()
        """,
        (str(company_id), Json(token) if token is not None else None),
    )


def _store_oauth_state(state: str, company_id: str, code_verifier: Optional[str]) -> None:
    execute(
        "DELETE FROM core.email_oauth_states WHERE created_at < now() - make_interval(mins => %s)",
        (OAUTH_STATE_TTL_MINUTES,),
    )
    execute(
        """
        INSERT INTO core.email_oauth_states (state, company_id, code_verifier, created_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (state)
        DO UPDATE SET company_id = EXCLUDED.company_id,
                      code_verifier = EXCLUDED.code_verifier,
                      created_at = now()
        """,
        (state, str(company_id), code_verifier),
    )


def _take_oauth_state(state: str) -> Optional[dict]:
    row = fetch_one(
        "SELECT company_id, code_verifier FROM core.email_oauth_states WHERE state = %s",
        (state,),
    )
    if row:
        execute("DELETE FROM core.email_oauth_states WHERE state = %s", (state,))
    return row


# --------------------------------------------------------------------------- config
def _merge_config(loaded: dict) -> dict:
    merged = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in (loaded or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def load_config(company_id: str) -> dict:
    return _merge_config(_read_config(company_id) or {})


def save_config(company_id: str, config: dict) -> dict:
    _write_config(company_id, config)
    return config


def public_config(company_id: str) -> dict:
    config = load_config(company_id)
    return {
        **config,
        "gmail_authenticated": _read_token(company_id) is not None,
        "google_configured": google_configured(),
        "redirect_uri": settings.google_oauth_redirect_uri,
    }


def update_config(company_id: str, data: dict) -> dict:
    config = load_config(company_id)
    if "enabled" in data:
        config["enabled"] = bool(data["enabled"])
    if "recipient_email" in data:
        config["recipient_email"] = str(data["recipient_email"] or "").strip()
    if "api_type" in data and data["api_type"] == "gmail_oauth":
        config["api_type"] = "gmail_oauth"

    incoming_alerts = data.get("alerts")
    if isinstance(incoming_alerts, dict):
        for key in config["alerts"]:
            if key in incoming_alerts:
                config["alerts"][key] = bool(incoming_alerts[key])

    incoming_cooldown = data.get("cooldown")
    if isinstance(incoming_cooldown, dict):
        if "enabled" in incoming_cooldown:
            config["cooldown"]["enabled"] = bool(incoming_cooldown["enabled"])
        if "minutes" in incoming_cooldown:
            try:
                minutes = int(incoming_cooldown["minutes"])
            except (TypeError, ValueError):
                minutes = 5
            config["cooldown"]["minutes"] = max(1, min(1440, minutes))

    return save_config(company_id, config)


def google_configured() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


# --------------------------------------------------------------------------- oauth
def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def create_auth_url(company_id: str) -> tuple[str, str]:
    if not GOOGLE_API_AVAILABLE:
        raise RuntimeError(
            "Google API libraries are not installed. Run: pip install google-auth-oauthlib google-api-python-client"
        )
    if not google_configured():
        raise RuntimeError(
            "Google OAuth client is not configured. Set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET in backend/.env."
        )

    flow = InstalledAppFlow.from_client_config(
        _client_config(), SCOPES, redirect_uri=settings.google_oauth_redirect_uri
    )
    state = f"{company_id}:{secrets.token_urlsafe(18)}"
    flow.oauth2session.state = state
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    # authorization_url() generates the PKCE verifier; persist it so the
    # callback can complete even on a different serverless instance.
    _store_oauth_state(state, str(company_id), getattr(flow, "code_verifier", None))
    return auth_url, state


def handle_oauth_callback(authorization_response: str, state: str) -> str:
    if not GOOGLE_API_AVAILABLE:
        raise RuntimeError("Google API libraries are not installed")
    if not state:
        raise RuntimeError(
            "Missing OAuth state. Start again from the Email alerts panel; do not open the callback URL directly."
        )

    pending = _take_oauth_state(state)
    if not pending:
        raise RuntimeError(
            "OAuth session expired or was already used. Start again from the Email alerts panel."
        )
    company_id = str(pending["company_id"])

    flow = InstalledAppFlow.from_client_config(
        _client_config(), SCOPES, redirect_uri=settings.google_oauth_redirect_uri
    )
    flow.oauth2session.state = state
    flow.code_verifier = pending.get("code_verifier")
    flow.fetch_token(authorization_response=authorization_response)

    creds = flow.credentials
    _write_token(company_id, json.loads(creds.to_json()))
    config = load_config(company_id)
    config["api_type"] = "gmail_oauth"
    save_config(company_id, config)
    return company_id


def _get_gmail_service(company_id: str):
    if not GOOGLE_API_AVAILABLE:
        return None, (
            "Google API libraries are not installed. Run: pip install google-auth-oauthlib "
            "google-api-python-client"
        )
    if not google_configured():
        return None, "Google OAuth client is not configured"

    token_info = _read_token(company_id)
    if not token_info:
        return None, "Not authenticated with Google. Click 'Sign in with Google' first."

    try:
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    except (ValueError, TypeError) as exc:
        return None, f"Failed to load Google credentials: {exc}"

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            _write_token(company_id, json.loads(creds.to_json()))
        except Exception as exc:
            return None, f"Failed to refresh Google credentials: {exc}. Re-authenticate."

    if not creds.valid:
        return None, "Google credentials are invalid. Re-authenticate."

    try:
        return build("gmail", "v1", credentials=creds), None
    except Exception as exc:
        return None, f"Failed to initialize Gmail API: {exc}"


# --------------------------------------------------------------------------- delivery
def _build_body(title: str, rows: list[tuple[str, str]], footer: Optional[str] = None) -> tuple[str, str]:
    table = "".join(
        f'<tr><td style="padding:8px;border:1px solid #ddd;"><strong>{html.escape(label)}</strong></td>'
        f'<td style="padding:8px;border:1px solid #ddd;">{html.escape(str(value))}</td></tr>'
        for label, value in rows
    )
    html_body = f"""\
<html><body style="font-family:Arial,sans-serif;padding:20px;color:#333;">
<div style="max-width:600px;margin:0 auto;border:1px solid #ddd;border-radius:8px;overflow:hidden;">
  <div style="background:#182456;color:#fff;padding:15px 20px;font-size:18px;font-weight:bold;">
    {html.escape(title)}
  </div>
  <div style="padding:20px;">
    <table style="width:100%;border-collapse:collapse;">{table}</table>
    <p style="color:#666;font-size:12px;margin-top:16px;">
      {html.escape(footer or "This is an automated alert from the Universal Industrial Platform.")}
    </p>
  </div>
</div></body></html>"""
    plain = "\n".join(f"{label}: {value}" for label, value in rows)
    return html_body, plain


def _send(company_id: str, subject: str, body_html: str, body_plain: str, recipient: str) -> tuple[bool, Optional[str]]:
    service, error = _get_gmail_service(company_id)
    if error:
        return False, error

    message = MIMEMultipart("alternative")
    message["To"] = recipient
    message["From"] = "me"
    message["Subject"] = subject
    message.attach(MIMEText(body_plain, "plain"))
    message.attach(MIMEText(body_html, "html"))

    try:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, None
    except Exception as exc:
        return False, f"Gmail API error: {exc}"


def send_test(company_id: str) -> tuple[bool, Optional[str]]:
    config = load_config(company_id)
    recipient = (config.get("recipient_email") or "").strip()
    if not recipient:
        return False, "No recipient email configured"
    body_html, body_plain = _build_body(
        "Email alert test",
        [("Status", "Test message"), ("Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))],
        footer="If you received this, email alerts are configured correctly.",
    )
    return _send(company_id, "[UCAP] Test email", body_html, body_plain, recipient)


def _on_cooldown(key: tuple[str, str, str], minutes: int) -> bool:
    now = time.time()
    last = _last_alert_times.get(key, 0)
    return (now - last) / 60.0 < minutes


def _mark_sent(key: tuple[str, str, str]) -> None:
    _last_alert_times[key] = time.time()


def _deliver_alert(company_id: str, instance_id: str, device_id: str, rule_type: str,
                   severity: str, message: str) -> None:
    config = load_config(company_id)
    recipient = (config.get("recipient_email") or "").strip()
    if not recipient:
        return
    title = ALERT_LABELS.get(rule_type, rule_type.replace("_", " ").title())
    rows = [
        ("Alert", title),
        ("Severity", severity),
        ("Application", instance_id),
        ("Source", device_id or "unknown"),
        ("Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Details", message),
    ]
    body_html, body_plain = _build_body(f"{title} - {severity.upper()}", rows)
    _send(company_id, f"[UCAP] {severity.upper()}: {title}", body_html, body_plain, recipient)


def notify(company_id, instance_id: str, device_id: Optional[str], rule_type: str,
           severity: str, message: str) -> None:
    config = load_config(str(company_id))
    if not config.get("enabled"):
        return
    if not config.get("alerts", {}).get(rule_type, True):
        return

    cooldown = config.get("cooldown") or {}
    key = (str(company_id), str(instance_id), rule_type)
    if cooldown.get("enabled", True):
        minutes = int(cooldown.get("minutes") or 5)
        with _lock:
            if _on_cooldown(key, minutes):
                return
            _mark_sent(key)

    args = (str(company_id), instance_id, device_id or "", rule_type, severity, message)
    # Serverless instances are frozen as soon as the response is sent, so a
    # background thread would often be killed before Gmail finishes. Send inline
    # on Vercel and keep the thread on long-running hosts.
    if os.environ.get("VERCEL"):
        _deliver_alert(*args)
    else:
        threading.Thread(target=_deliver_alert, args=args, daemon=True).start()
