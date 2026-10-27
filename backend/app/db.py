"""Database access layer — thin psycopg2 pool with dict-row helpers."""
from __future__ import annotations

import os
import queue
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import PoolError

from .config import settings

_pool: Optional["_SimplePool"] = None
_lock = threading.Lock()
POOL_MAX_CONNECTIONS = max(10, int(os.environ.get("PLATFORM_DB_POOL_MAX", "50")))


class _SimplePool:
    """Pool that opens/closes connections outside its lock.

    psycopg2's ThreadedConnectionPool holds a global lock while connecting, so a
    burst of source threads serializes behind connection setup. That makes the
    dashboard look frozen. Here only the counters are locked.
    """

    def __init__(self, maxconn: int, dsn: str):
        self.maxconn = maxconn
        self.dsn = dsn
        self._idle: "queue.LifoQueue" = queue.LifoQueue()
        self._count = 0
        self._lock = threading.Lock()

    def _new(self):
        return psycopg2.connect(dsn=self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def getconn(self, timeout: float = 10.0):
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            create = self._count < self.maxconn
            if create:
                self._count += 1
        if create:
            try:
                return self._new()
            except Exception:
                with self._lock:
                    self._count = max(0, self._count - 1)
                raise
        try:
            return self._idle.get(timeout=max(0.1, timeout))
        except queue.Empty as exc:
            raise PoolError("connection pool exhausted") from exc

    def putconn(self, conn, close: bool = False) -> None:
        if close:
            try:
                conn.close()
            except Exception:
                pass
            with self._lock:
                self._count = max(0, self._count - 1)
            return
        self._idle.put(conn)

    def closeall(self) -> None:
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:
                pass


def _get_pool() -> "_SimplePool":
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = _SimplePool(POOL_MAX_CONNECTIONS, settings.database_url)
    return _pool


def _borrow(pool: "_SimplePool", timeout: float = 10.0):
    return pool.getconn(timeout=timeout)


@contextmanager
def get_cursor(commit: bool = False) -> Iterator["psycopg2.extras.RealDictCursor"]:
    """Borrow a pooled connection, yield a dict cursor, commit/rollback, return it."""
    pool = _get_pool()
    conn = _borrow(pool)
    broken = False
    try:
        # RealDictCursor was set as the connection cursor factory.
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
            else:
                conn.rollback()
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
