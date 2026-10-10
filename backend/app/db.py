"""Database access layer — thin psycopg2 pool with dict-row helpers."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from .config import settings

_pool: Optional[ThreadedConnectionPool] = None
_lock = threading.Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=settings.database_url,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _pool


@contextmanager
def get_cursor(commit: bool = False) -> Iterator["psycopg2.extras.RealDictCursor"]:
    """Borrow a pooled connection, yield a dict cursor, commit/rollback, return it."""
    pool = _get_pool()
    conn = pool.getconn()
    broken = False
    try:
        # RealDictCursor was set as the connection cursor factory.
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    except psycopg2.Error:
        # A dropped / unrecoverable connection: don't return it to the pool.
        broken = True
        raise
    finally:
        pool.putconn(conn, close=broken)


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with get_cursor(commit=True) as cur:
        cur.execute(sql, params)
        return cur.rowcount


def execute_returning(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    with get_cursor(commit=True) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
