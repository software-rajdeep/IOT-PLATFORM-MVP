"""Create the database (if missing) and apply schema + seed.

Usage (from the backend/ directory):
    python scripts/init_db.py

Reads DATABASE_URL from the environment or backend/.env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
from psycopg2.extensions import parse_dsn

from app.config import settings

DATABASE_DIR = Path(__file__).resolve().parent.parent / "database"


def _split_dsn(dsn: str) -> dict:
    return parse_dsn(dsn)


def ensure_database() -> None:
    parts = _split_dsn(settings.database_url)
    target = parts.get("dbname")
    maintenance = dict(parts)
    maintenance["dbname"] = "postgres"
    conn = psycopg2.connect(**maintenance)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone():
                print(f"[init_db] database '{target}' already exists")
            else:
                cur.execute(f'CREATE DATABASE "{target}"')
                print(f"[init_db] created database '{target}'")
    finally:
        conn.close()


def _customer_demo_dsn() -> dict:
    """DSN for the demo customer database on the same PostgreSQL server."""
    parts = _split_dsn(settings.database_url)
    parts["dbname"] = os.environ.get("CUSTOMER_DEMO_DB_NAME", "ucap_customer_corp")
    return parts


def ensure_customer_demo() -> None:
    """Create the separate demo customer database (many tables + random data)."""
    parts = _split_dsn(settings.database_url)
    target = _customer_demo_dsn().get("dbname")
    maintenance = dict(parts)
    maintenance["dbname"] = "postgres"
    conn = psycopg2.connect(**maintenance)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone():
                print(f"[init_db] customer demo database '{target}' already exists")
            else:
                cur.execute(f'CREATE DATABASE "{target}"')
                print(f"[init_db] created customer demo database '{target}'")
    finally:
        conn.close()
    customer_sql = DATABASE_DIR / "customer_demo.sql"
    if customer_sql.exists():
        conn = psycopg2.connect(**_customer_demo_dsn())
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(customer_sql.read_text(encoding="utf-8"))
            print(f"[init_db] refreshed customer demo data ({customer_sql.name})")
        finally:
            conn.close()


def apply_sql(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    conn = psycopg2.connect(settings.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print(f"[init_db] applied {path.name}")
    finally:
        conn.close()


def apply_migrations() -> None:
    """Apply database/migrations/NNN_*.sql in order, once each (tracked in core.schema_migrations)."""
    conn = psycopg2.connect(settings.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS core.schema_migrations ("
                "name VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT name FROM core.schema_migrations")
            done = {r[0] for r in cur.fetchall()}
        conn.commit()
        for path in sorted((DATABASE_DIR / "migrations").glob("*.sql")):
            if path.name in done:
                continue
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO core.schema_migrations (name) VALUES (%s)", (path.name,))
            conn.commit()
            print(f"[init_db] migrated {path.name}")
    finally:
        conn.close()


def main() -> None:
    print(f"[init_db] target = {settings.database_url}")
    ensure_database()
    ensure_customer_demo()
    apply_sql(DATABASE_DIR / "schema.sql")
    apply_sql(DATABASE_DIR / "seed.sql")
    apply_migrations()
    print("[init_db] done")


if __name__ == "__main__":
    main()
