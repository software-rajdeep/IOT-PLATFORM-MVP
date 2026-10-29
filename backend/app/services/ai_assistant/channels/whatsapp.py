"""WhatsApp Cloud API adapter and identity linking.

Until a Meta developer account exists, run WHATSAPP_DRIVER=mock. Replies are
queued in core.ai_outbox and logged, never sent. Switching to WHATSAPP_DRIVER=graph
requires only environment variables and a public HTTPS webhook.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ....config import settings
from ....db import execute, execute_returning, fetch_all, fetch_one
from ..context import AIContext
from ..service import get_chat_service

LINK_PREFIX = "LINK"
MAX_TEXT = 4096


def verify_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """Verify Meta's X-Hub-Signature-256 header."""
    if not settings.whatsapp_app_secret:
        return bool(settings.whatsapp_allow_unsigned)
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.split("=", 1)[1])


def parse_messages(payload: Any) -> list[dict]:
    messages = []
    for entry in (payload or {}).get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for item in value.get("messages", []) or []:
                if item.get("type") not in ("text", "interactive"):
                    continue
                text = (item.get("text") or {}).get("body")
                if text is None and item.get("interactive"):
                    text = (item.get("interactive") or {}).get("button_reply", {}).get("title")
                messages.append({
                    "provider_message_id": item.get("id") or f"wa-{item.get('from')}-{item.get('timestamp')}",
                    "from": str(item.get("from") or ""),
                    "text": str(text or "").strip(),
                    "timestamp": item.get("timestamp"),
                })
    return [message for message in messages if message["from"] and message["text"]]


def record_event(provider_event_id: str, payload: Any) -> bool:
    """Insert a webhook event once. Returns False when it is a duplicate."""
    try:
        row = execute_returning(
            "INSERT INTO core.ai_webhook_events (channel, provider_event_id, payload) "
            "VALUES ('whatsapp', %s, %s) ON CONFLICT (channel, provider_event_id) DO NOTHING "
            "RETURNING event_id",
            (provider_event_id, json.dumps(payload, default=str)),
        )
        return bool(row)
    except Exception:
        return False


def mark_event(provider_event_id: str, status: str, error: Optional[str] = None) -> None:
    execute(
        "UPDATE core.ai_webhook_events SET status = %s, error = %s, processed_at = now() "
        "WHERE channel = 'whatsapp' AND provider_event_id = %s",
        (status, error, provider_event_id),
    )


def identity_for(external_user_id: str) -> Optional[dict]:
    return fetch_one(
        "SELECT identity_id, company_id, user_id, external_user_id, display_name, status "
        "FROM core.ai_channel_identities WHERE channel = 'whatsapp' AND external_user_id = %s",
        (external_user_id,),
    )


def create_link_code(company_id: str, user_id: str) -> dict:
    code = secrets.token_hex(4).upper()
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    execute(
        "INSERT INTO core.ai_link_codes (code, company_id, user_id, expires_at) VALUES (%s, %s, %s, %s)",
        (code, company_id, user_id, expires),
    )
    return {"code": code, "expires_at": expires.isoformat()}


def link_identity(code: str, external_user_id: str, display_name: Optional[str] = None) -> Optional[dict]:
    row = fetch_one(
        "SELECT code, company_id, user_id FROM core.ai_link_codes "
        "WHERE upper(code) = upper(%s) AND used_at IS NULL AND expires_at > now()",
        (code.strip(),),
    )
    if not row:
        return None
    execute("UPDATE core.ai_link_codes SET used_at = now() WHERE code = %s", (row["code"],))
    execute(
        """
        INSERT INTO core.ai_channel_identities
            (company_id, user_id, channel, external_user_id, display_name, status, opt_in_at)
        VALUES (%s, %s, 'whatsapp', %s, %s, 'active', now())
        ON CONFLICT (channel, external_user_id) DO UPDATE
        SET company_id = EXCLUDED.company_id, user_id = EXCLUDED.user_id,
            display_name = COALESCE(EXCLUDED.display_name, core.ai_channel_identities.display_name),
            status = 'active', opt_in_at = now(), opt_out_at = NULL, updated_at = now()
        """,
        (row["company_id"], row["user_id"], external_user_id, display_name),
    )
    return {"company_id": str(row["company_id"]), "user_id": str(row["user_id"])}


def _load_user(company_id: str, user_id: str) -> Optional[dict]:
    return fetch_one(
        "SELECT u.id, u.company_id, u.username, u.email, u.status, r.role_name AS role "
        "FROM core.users u LEFT JOIN core.roles r ON r.role_id = u.role_id "
        "WHERE u.id = %s AND u.company_id = %s AND u.status = 'active'",
        (user_id, company_id),
    )


def enqueue_text(company_id: Optional[str], to: str, body: str) -> None:
    execute(
        "INSERT INTO core.ai_outbox (company_id, channel, recipient, payload) "
        "VALUES (%s, 'whatsapp', %s, %s)",
        (company_id, to, json.dumps({"text": (body or "")[:MAX_TEXT]})),
    )


def _post_graph(to: str, body: str) -> tuple[bool, Optional[str]]:
    if settings.whatsapp_driver == "mock":
        print(f"[whatsapp:mock] to={to} text={body[:160]!r}")
        return True, None
    if settings.whatsapp_driver != "graph":
        return False, "WhatsApp driver is disabled"
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        return False, "WhatsApp Cloud API is not configured"
    url = (f"https://graph.facebook.com/{settings.whatsapp_graph_version}/"
           f"{settings.whatsapp_phone_number_id}/messages")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body[:MAX_TEXT]},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + settings.whatsapp_access_token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
        return True, None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(exc)
        return False, detail
    except Exception as exc:
        return False, str(exc)[:500]


def flush_outbox(limit: int = 20) -> int:
    rows = fetch_all(
        "SELECT outbox_id, company_id, recipient, payload, attempts FROM core.ai_outbox "
        "WHERE status IN ('pending', 'retry') AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
        "ORDER BY outbox_id LIMIT %s",
        (max(1, min(int(limit), 100)),),
    )
    sent = 0
    for row in rows:
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        text = str(payload.get("text") or "")
        ok, error = _post_graph(str(row["recipient"]), text)
        attempts = int(row.get("attempts") or 0) + 1
        if ok:
            execute(
                "UPDATE core.ai_outbox SET status = 'sent', attempts = %s, sent_at = now(), error = NULL "
                "WHERE outbox_id = %s",
                (attempts, row["outbox_id"]),
            )
            sent += 1
        else:
            delay = min(300, 15 * attempts)
            execute(
                "UPDATE core.ai_outbox SET status = 'retry', attempts = %s, next_attempt_at = %s, error = %s "
                "WHERE outbox_id = %s",
                (attempts, datetime.now(timezone.utc) + timedelta(seconds=delay), error, row["outbox_id"]),
            )
    return sent


def _handle_link(text: str, external_user_id: str) -> Optional[str]:
    if not text.upper().startswith(LINK_PREFIX + " "):
        return None
    code = text.split(" ", 1)[1].strip()
    linked = link_identity(code, external_user_id)
    if linked:
        return ("This WhatsApp number is now linked to your Rajdeep account. "
                "Ask me about your machines, readings, limits or alerts.")
    return "That linking code is invalid or expired. Generate a new code from the AI Assistant page."


def process_text(external_user_id: str, text: str) -> Optional[str]:
    linked_reply = _handle_link(text, external_user_id)
    if linked_reply is not None:
        enqueue_text(None, external_user_id, linked_reply)
        return linked_reply

    lower = text.strip().lower()
    identity = identity_for(external_user_id)
    if lower in ("stop", "unsubscribe", "opt out"):
        if identity:
            execute(
                "UPDATE core.ai_channel_identities SET status = 'opted_out', opt_out_at = now(), "
                "updated_at = now() WHERE identity_id = %s",
                (identity["identity_id"],),
            )
            enqueue_text(identity["company_id"], external_user_id,
                         "You have been unsubscribed. Send START to opt in again.")
        return "opted out"
    if lower in ("start", "subscribe", "opt in") and identity:
        execute(
            "UPDATE core.ai_channel_identities SET status = 'active', opt_in_at = now(), "
            "opt_out_at = NULL, updated_at = now() WHERE identity_id = %s",
            (identity["identity_id"],),
        )
        enqueue_text(identity["company_id"], external_user_id,
                     "You are opted in again. Ask me about your machines.")
        return "opted in"

    if not identity or identity.get("status") != "active":
        reply = ("This WhatsApp number is not linked to a Rajdeep account. Open the AI Assistant page, "
                 "generate a linking code, then send: LINK <code>")
        enqueue_text(None, external_user_id, reply)
        return reply

    company_id = str(identity["company_id"])
    if not settings.ai_enabled:
        reply = "The Rajdeep AI Assistant is disabled on this server. Contact your administrator."
        enqueue_text(company_id, external_user_id, reply)
        return reply
    user = _load_user(company_id, str(identity["user_id"])) if identity.get("user_id") else None
    if not user:
        reply = "Your linked user is no longer active. Contact your administrator."
        enqueue_text(company_id, external_user_id, reply)
        return reply

    ctx = AIContext(
        company_id=company_id,
        user_id=str(user["id"]),
        username=str(user.get("username") or ""),
        role=user.get("role"),
    )
    try:
        result = get_chat_service().chat(ctx, text, channel="whatsapp")
        reply = result.get("message") or "I could not produce an answer from the available data."
    except Exception:
        reply = "I could not reach the assistant right now. Please try again shortly."
    enqueue_text(company_id, external_user_id, reply)
    return reply
