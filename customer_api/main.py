"""Standalone "customer line API" — simulates a customer's own server + database.

Treats any PostgreSQL database (e.g. a Neon project) as the customer's database and
exposes its newest row over an authenticated JSON endpoint, exactly the way the UCAP
platform's "Customer API (pull)" source connects:

    platform (database_api driver, customer mode)
      -> GET /api/latest   (X-Api-Key, X-Api-Secret headers)
      -> reads the newest row from the configured table
      -> returns it as JSON; the platform maps a field to measurement_raw

Configuration is all environment (nothing customer-specific is hard-coded):

    DATABASE_URL        postgresql://...   (the customer's database, e.g. Neon)
    TABLE               schema.table       (default public.rubber)
    ORDER_BY            column             (default id)
    CUSTOMER_API_KEY    the API key the customer issues        (default neon-customer-key)
    CUSTOMER_API_SECRET the secret key the customer issues     (default neon-customer-secret)

Run (from the repo root):

    $env:DATABASE_URL = "postgresql://...neon.../cd22-35?sslmode=require"
    $env:CUSTOMER_API_KEY = "neon-customer-key"
    $env:CUSTOMER_API_SECRET = "neon-customer-secret"
    python -m uvicorn customer_api.main:app --host 0.0.0.0 --port 8090
"""
from __future__ import annotations

import os
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Header, HTTPException

DATABASE_URL = os.environ["DATABASE_URL"]
TABLE = os.environ.get("TABLE", "public.rubber")
ORDER_BY = os.environ.get("ORDER_BY", "id")
API_KEY = os.environ.get("CUSTOMER_API_KEY", "neon-customer-key")
API_SECRET = os.environ.get("CUSTOMER_API_SECRET", "neon-customer-secret")

app = FastAPI(title="Customer line API")


def _quote_ident(name: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in str(name).split("."))


def _require_auth(api_key: Optional[str], api_secret: Optional[str]) -> None:
    if api_key != API_KEY or api_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API key or secret")


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=5, cursor_factory=psycopg2.extras.RealDictCursor)


def _json_safe(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


@app.get("/api/latest")
def latest(x_api_key: Optional[str] = Header(default=None), x_api_secret: Optional[str] = Header(default=None)) -> dict:
    """The customer's newest reading row, returned under their own column names."""
    _require_auth(x_api_key, x_api_secret)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_quote_ident(TABLE)} ORDER BY {_quote_ident(ORDER_BY)} DESC LIMIT 1")
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No readings")
    return {k: _json_safe(v) for k, v in row.items()}


@app.get("/tables")
def tables(x_api_key: Optional[str] = Header(default=None), x_api_secret: Optional[str] = Header(default=None)) -> dict:
    """Table/column listing so the platform's discovery can offer the columns for mapping."""
    _require_auth(x_api_key, x_api_secret)
    schema = TABLE.split(".")[0] if "." in TABLE else "public"
    name = TABLE.split(".")[-1]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (schema, name),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {"tables": [{"table": TABLE, "columns": [{"column": r["column_name"], "type": r["data_type"]} for r in rows]}]}


@app.get("/health")
def health() -> dict:
    return {"ok": True}
