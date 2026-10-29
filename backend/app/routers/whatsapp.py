"""WhatsApp webhook and linking routes.

The webhook is public and authenticated with Meta's X-Hub-Signature-256. While no
Meta developer account exists, WHATSAPP_DRIVER=mock plus the authenticated
/test-message route let the whole flow run locally without sending anything.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..config import settings
from ..db import execute, fetch_all
from ..deps import get_current_user, require_company
from ..services.ai_assistant.channels import whatsapp

router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp"])


def _process_message(message: dict) -> None:
    try:
        whatsapp.process_text(message["from"], message["text"])
        whatsapp.mark_event(message["provider_message_id"], "processed")
    except Exception as exc:
        whatsapp.mark_event(message["provider_message_id"], "failed", str(exc)[:500])
    finally:
        try:
            whatsapp.flush_outbox(20)
        except Exception:
            pass


@router.get("/webhook", response_class=PlainTextResponse)
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    if not settings.whatsapp_verify_token:
        raise HTTPException(status_code=503, detail="WhatsApp verify token is not configured")
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks) -> dict:
    raw = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not whatsapp.verify_signature(raw, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    messages = whatsapp.parse_messages(payload)
    queued = 0
    for message in messages:
        if whatsapp.record_event(message["provider_message_id"], payload):
            background.add_task(_process_message, message)
            queued += 1
    return {"ok": True, "received": len(messages), "queued": queued}


class LinkCodeResponse(BaseModel):
    code: str
    expires_at: str
    instructions: str


@router.post("/link-code", response_model=LinkCodeResponse)
def link_code(user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    data = whatsapp.create_link_code(company_id, str(user["id"]))
    return {
        **data,
        "instructions": f"Send this WhatsApp message to the Rajdeep number: LINK {data['code']}",
    }


@router.get("/identities")
def identities(user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    rows = fetch_all(
        "SELECT identity_id, user_id, external_user_id, display_name, status, opt_in_at, opt_out_at, created_at "
        "FROM core.ai_channel_identities WHERE company_id = %s AND channel = 'whatsapp' ORDER BY created_at DESC",
        (company_id,),
    )
    for row in rows:
        row["identity_id"] = str(row["identity_id"])
        if row.get("user_id"):
            row["user_id"] = str(row["user_id"])
        for key in ("opt_in_at", "opt_out_at", "created_at"):
            if row.get(key):
                row[key] = row[key].isoformat()
    return {"identities": rows}


@router.delete("/identities/{identity_id}")
def remove_identity(identity_id: str, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Administrator access required")
    execute(
        "UPDATE core.ai_channel_identities SET status = 'blocked', updated_at = now() "
        "WHERE identity_id = %s AND company_id = %s",
        (identity_id, company_id),
    )
    return {"ok": True}


class TestMessage(BaseModel):
    from_number: str
    text: str


@router.post("/test-message")
def test_message(body: TestMessage, user: dict = Depends(get_current_user)) -> dict:
    """Local end-to-end test without Meta. Requires an authenticated admin."""
    company_id = require_company(user)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Administrator access required")
    if settings.whatsapp_driver != "mock":
        raise HTTPException(status_code=400, detail="test-message is only available with WHATSAPP_DRIVER=mock")
    if not body.from_number.strip() or not body.text.strip():
        raise HTTPException(status_code=400, detail="from_number and text are required")
    reply = whatsapp.process_text(body.from_number.strip(), body.text.strip())
    whatsapp.flush_outbox(20)
    return {"ok": True, "company_id": company_id, "reply": reply}

