"""Authentication routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..db import fetch_one
from ..deps import get_current_user
from ..security import create_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


def _company_public(company_id) -> dict | None:
    if not company_id:
        return None
    row = fetch_one(
        "SELECT company_id, company_name, status FROM core.companies WHERE company_id = %s",
        (company_id,),
    )
    if not row:
        return None
    return {"company_id": str(row["company_id"]), "company_name": row["company_name"], "status": row["status"]}


@router.post("/login")
def login(body: LoginRequest) -> dict:
    identifier = body.email.strip()
    user = fetch_one(
        """
        SELECT u.id, u.company_id, u.username, u.email, u.status, u.password_hash,
               r.role_name AS role
        FROM core.users u
        LEFT JOIN core.roles r ON r.role_id = u.role_id
        WHERE lower(u.email) = lower(%s) OR lower(u.username) = lower(%s)
        LIMIT 1
        """,
        (identifier, identifier),
    )
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is not active")

    token = create_token({"uid": str(user["id"])})
    return {
        "token": token,
        "user": {
            "id": str(user["id"]),
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
        },
        "company": _company_public(user["company_id"]),
    }


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    return {
        "user": {
            "id": str(user["id"]),
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
        },
        "company": _company_public(user["company_id"]),
    }
