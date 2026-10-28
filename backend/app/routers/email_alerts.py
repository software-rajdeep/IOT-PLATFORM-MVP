"""Email alert settings and Gmail OAuth routes."""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..deps import get_current_user, require_company
from ..services import email_alerts

router = APIRouter(prefix="/api/v1/email-alerts", tags=["email-alerts"])
legacy_router = APIRouter(tags=["email-alerts"])


class EmailCooldown(BaseModel):
    enabled: bool | None = None
    minutes: int | None = None


class EmailAlertsConfig(BaseModel):
    enabled: bool | None = None
    recipient_email: str | None = None
    alerts: dict | None = None
    cooldown: EmailCooldown | None = None


def _admin_company(user: dict) -> str:
    company_id = require_company(user)
    if user.get("role") not in ("superadmin", "admin"):
        raise HTTPException(status_code=403, detail="Email alert settings require an administrator account")
    return company_id


@router.get("/config")
def get_config(user: dict = Depends(get_current_user)) -> dict:
    return email_alerts.public_config(require_company(user))


@router.put("/config")
def put_config(body: EmailAlertsConfig, user: dict = Depends(get_current_user)) -> dict:
    company_id = _admin_company(user)
    data = body.model_dump(exclude_none=True)
    email_alerts.update_config(company_id, data)
    return email_alerts.public_config(company_id)


@router.post("/test")
def test_email(user: dict = Depends(get_current_user)) -> dict:
    company_id = _admin_company(user)
    success, error = email_alerts.send_test(company_id)
    if not success:
        raise HTTPException(status_code=400, detail=error or "Failed to send test email")
    return {"message": "Test email sent successfully"}


@router.get("/auth-url")
def auth_url(user: dict = Depends(get_current_user)) -> dict:
    company_id = _admin_company(user)
    try:
        url, _ = email_alerts.create_auth_url(company_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"auth_url": url}


def _page(title: str, message: str, ok: bool) -> str:
    color = "#2e7d32" if ok else "#c62828"
    mark = "&#10003;" if ok else "&#10007;"
    return f"""\
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; display: flex; justify-content: center;
       align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }}
.card {{ background: #fff; padding: 40px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.15);
        text-align: center; max-width: 420px; }}
h2 {{ color: {color}; margin-top: 0; }} .mark {{ font-size: 64px; color: {color}; }}
p {{ color: #555; line-height: 1.5; }}
</style></head><body>
<div class="card">
<div class="mark">{mark}</div>
<h2>{html.escape(title)}</h2>
<p>{html.escape(message)}</p>
</div></body></html>"""


def _callback(request: Request) -> HTMLResponse:
    error = request.query_params.get("error_description") or request.query_params.get("error")
    if error:
        return HTMLResponse(
            _page("Authentication failed", f"{error} Please close this window and try again.", False),
            status_code=200,
        )
    state = request.query_params.get("state", "")
    try:
        email_alerts.handle_oauth_callback(str(request.url), state)
    except Exception as exc:
        return HTMLResponse(
            _page("Authentication failed", f"{exc} Please close this window and try again.", False),
            status_code=200,
        )
    return HTMLResponse(
        _page("Authentication successful", "Your Google account is connected. You can close this window.", True),
        status_code=200,
    )


@router.get("/oauth-callback", response_class=HTMLResponse)
def oauth_callback(request: Request) -> HTMLResponse:
    return _callback(request)


@legacy_router.get("/email-alerts/oauth-callback", response_class=HTMLResponse)
def oauth_callback_legacy(request: Request) -> HTMLResponse:
    return _callback(request)
