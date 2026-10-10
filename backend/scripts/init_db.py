"""Create the database (if missing) and apply schema + seed.

Usage (from the backend/ directory):
    python scripts/init_db.py

Reads DATABASE_URL from the environment or backend/.env.
"""
from __future__ import annotations

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


def main() -> None:
    print(f"[init_db] target = {settings.database_url}")
    ensure_database()
    apply_sql(DATABASE_DIR / "schema.sql")
    apply_sql(DATABASE_DIR / "seed.sql")
    print("[init_db] done")


if __name__ == "__main__":
    main()
