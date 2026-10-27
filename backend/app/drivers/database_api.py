"""BLOCK 1 — Database API connector

Reads measurements from a database/API instead of serial hardware.

THE KEY IDEA: we do NOT know the customer's schema. We know the TARGET fields
the platform needs, and the customer's source is MAPPED onto them:

    platform target field      <-  customer source
    -----------------------        --------------------------
    measurement_raw            <-  DB column  (or API JSON path)
    timestamp      (optional)  <-  DB column  (or API JSON path)
    device_id      (optional)  <-  DB column  (or API JSON path)

`measurement_raw` is the only required target — it is normalized upstream into
`displacement.value_mm`. This is how we "find the correct data readings" in an
arbitrary customer database.

Two source modes:
1. INTERNAL — a database we can reach directly (our DB, or credentials provided).
   Discovery lists tables/columns; the mapping picks columns.
2. CUSTOMER — the customer's own server, reachable only via API (key + secret).
   If the API exposes a `/tables` listing (our generic customer API does), discovery
   returns every table/column; otherwise it lists the JSON keys of the latest response.

Every connector returns the same customer-agnostic raw value dict:

    {
      "device_id": "DEV001",
      "timestamp": "2026-10-08T10:30:22.432+05:30",
      "measurement_raw": 151,
      "communication_status": "online"
    }
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from .base import DeviceDriver, DriverError
from .registry import register

DEFAULT_PORT = 5432
MAX_RESPONSE_BYTES = 1_000_000

# The target fields the platform can consume. Keys are TARGET field names.
TARGET_FIELDS = ("measurement_raw", "timestamp", "device_id")

DEFAULT_RAW_COLUMN = "measurement_raw"
_connection_pools = {}
_connection_pools_lock = threading.Lock()


def _lookup(data: Any, path: str):
    """Walk a dict/list using dot notation, e.g. data.latest.value or 0.raw."""
    current = data
    for part in str(path).split("."):
        part = part.strip()
        if not part:
            continue
        try:
            current = current[int(part)]
        except (ValueError, TypeError, IndexError, KeyError):
            try:
                current = current[part]
            except (KeyError, IndexError, TypeError):
                return None
    return current


def _json_safe(value: Any) -> Any:
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def quote_ident(name: str) -> str:
    """Quote a possibly schema-qualified identifier: a.b -> \"a\".\"b\"."""
    return ".".join('"' + part.replace('"', '""') + '"' for part in str(name).split("."))


def _flatten_keys(data: Any, prefix: str = "") -> list[str]:
    """List every dot-notation JSON path in a payload (for mapping)."""
    keys: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.append(path)
            if isinstance(value, (dict, list)):
                keys.extend(_flatten_keys(value, path))
    elif isinstance(data, list) and data:
        keys.extend(_flatten_keys(data[0], f"{prefix}.0" if prefix else "0"))
    return keys


@register
class DatabaseApiConnector(DeviceDriver):
    driver_key = "database_api"

    def __init__(self, connection: Optional[dict] = None, model: Optional[dict] = None):
        super().__init__(connection, model)
        db = dict(self.config.get("database") or {})
        self.mode = str(db.get("mode") or self.config.get("mode") or "internal").lower()

        # internal mode (direct database access)
        self.host = db.get("host") or self.config.get("host")
        self.port = int(db.get("port") or self.config.get("port") or DEFAULT_PORT)
        self.database = db.get("database") or db.get("name") or self.config.get("database")
        self.table = db.get("table") or self.config.get("table")
        self.order_by = db.get("order_by") or self.config.get("order_by")
        self.username = db.get("username") or db.get("user") or self.config.get("username")
        self.password = db.get("password") or self.config.get("password")
        # sslmode (e.g. "require" for managed Postgres such as Neon). None = libpq default.
        self.sslmode = db.get("sslmode") or self.config.get("sslmode")

        # customer mode (their server, API key + secret)
        self.api_url = db.get("api_url") or self.config.get("api_url")
        self.api_key = db.get("api_key") or self.config.get("api_key")
        self.api_secret = db.get("api_secret") or db.get("secret_key") or self.config.get("api_secret")

        # target-field mapping: {target_field: source_column_or_json_path}
        mapping = db.get("mapping") or self.config.get("mapping") or {}
        self.mapping = {k: v for k, v in mapping.items() if v not in (None, "")}
        # backwards compatibility with the earlier single-field config
        if "measurement_raw" not in self.mapping:
            legacy = db.get("column") or db.get("value_field") or self.config.get("value_field")
            if legacy:
                self.mapping["measurement_raw"] = legacy
        if "timestamp" not in self.mapping and db.get("order_by"):
            self.mapping.setdefault("timestamp", db.get("order_by"))

        # Replay mode: walk through historical rows instead of always reading the
        # newest one. Used when the real sensor is offline but old readings exist.
        #   "replay": {"enabled": true, "from": "...", "to": "...",
        #              "step": 20, "loop": true, "timestamp": "now"}
        replay = db.get("replay") or self.config.get("replay") or {}
        if isinstance(replay, bool):
            replay = {"enabled": replay}
        self.replay = bool(replay.get("enabled", False))
        self.replay_from = replay.get("from") or replay.get("start")
        self.replay_to = replay.get("to") or replay.get("end")
        self.replay_step = max(1, int(replay.get("step") or 1))
        self.replay_loop = bool(replay.get("loop", True))
        self.replay_timestamp = str(replay.get("timestamp") or "now").lower()
        self.replay_time_column = replay.get("time_column")
        self.replay_order_by = replay.get("order_by") or self.order_by
        self._time_column_cache = None

        self.timeout = float(self.config.get("timeout", 5))
        self.simulate = bool(self.config.get("simulate", not self._configured()))
        self._t0 = time.time()

    # -- mapping helpers ---------------------------------------------------
    @property
    def raw_source(self) -> str:
        """The customer source mapped to measurement_raw (required target)."""
        return self.mapping.get("measurement_raw") or DEFAULT_RAW_COLUMN

    def _configured(self) -> bool:
        if self.mode == "customer":
            return bool(self.api_url and self.api_key and self.api_secret)
        return bool(self.host and self.database and self.table)

    # ---- internal mode: direct database ---------------------------------
    def _connect(self):
        try:
            import psycopg2
            from psycopg2.pool import ThreadedConnectionPool
        except ImportError as exc:  # pragma: no cover
            raise DriverError("psycopg2 is required for an internal database connection.") from exc
        from ..services.source_guard import SourceBlocked, check_host
        try:
            check_host(self.host, self.port, self.database)
        except SourceBlocked as exc:
            raise DriverError(str(exc)) from exc
        kwargs = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.username,
            "password": self.password,
            "connect_timeout": max(1, math.ceil(self.timeout)),
        }
        if self.sslmode:
            kwargs["sslmode"] = self.sslmode
        key = tuple(sorted(kwargs.items()))
        with _connection_pools_lock:
            pool = _connection_pools.get(key)
            if pool is None:
                pool = ThreadedConnectionPool(minconn=1, maxconn=4, **kwargs)
                _connection_pools[key] = pool
        return pool, pool.getconn()

    @staticmethod
    def _release(connection_info, broken: bool = False) -> None:
        pool, conn = connection_info
        pool.putconn(conn, close=broken)

    @contextmanager
    def _database_connection(self):
        connection_info = self._connect()
        broken = False
        try:
            yield connection_info[1]
        except Exception:
            broken = True
            raise
        finally:
            if not broken:
                try:
                    connection_info[1].rollback()
                except Exception:
                    broken = True
            self._release(connection_info, broken=broken)

    def _read_internal(self) -> dict:
        if not self.host or not self.database or not self.table:
            raise DriverError("Internal database connection requires host, database and table.")

        selected = {"measurement_raw": quote_ident(self.raw_source)}
        for target in ("timestamp", "device_id"):
            if self.mapping.get(target):
                selected[target] = quote_ident(self.mapping[target])

        select_list = ", ".join(f"{expr} AS {quote_ident(target)}" for target, expr in selected.items())
        order_col = self.order_by or self.mapping.get("timestamp")
        order_clause = f" ORDER BY {quote_ident(order_col)} DESC" if order_col else ""
        sql = f"SELECT {select_list} FROM {quote_ident(self.table)}{order_clause} LIMIT 1"

        with self._database_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                columns = [d[0] for d in cur.description] if cur.description else []
        if not row:
            return {"measurement_raw": None}
        data = dict(zip(columns, row))
        return data

    def _api_base(self) -> str:
        parts = urllib.parse.urlsplit(self.api_url or "")
        return f"{parts.scheme}://{parts.netloc}"

    def _with_table(self, url: str) -> str:
        """Append ?table=<configured table> when the customer API is table-addressable."""
        if not self.table:
            return url
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        query.setdefault("table", self.table)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
        )

    def _request_json(self, url: str) -> Any:
        if not (url and self.api_key and self.api_secret):
            raise DriverError("Customer API connection requires api_url, api_key and api_secret.")
        from ..services.source_guard import SourceBlocked, check_url
        try:
            check_url(url)
        except SourceBlocked as exc:
            raise DriverError(str(exc)) from exc
        headers = {
            "Accept": "application/json",
            "X-Api-Key": self.api_key,
            "X-Api-Secret": self.api_secret,
            "Authorization": f"Bearer {self.api_secret}",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception as exc:
            raise DriverError(f"Customer API request failed: {exc}") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise DriverError("Customer API response is larger than 1 MB")
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise DriverError("Customer API did not return JSON") from exc

    def _fetch_json(self) -> Any:
        return self._request_json(self._with_table(self.api_url))

    def _fetch_tables(self) -> Optional[list]:
        """Ask the API for a table listing (GET <base>/tables). None when unsupported."""
        try:
            data = self._request_json(f"{self._api_base()}/tables")
        except Exception:
            return None
        if isinstance(data, dict) and isinstance(data.get("tables"), list):
            return [t for t in data["tables"] if isinstance(t, dict) and t.get("columns")]
        return None

    def _read_customer(self) -> dict:
        payload = self._fetch_json()

        out: dict = {}
        for target in TARGET_FIELDS:
            source = self.mapping.get(target)
            if source:
                out[target] = _lookup(payload, source)
        return out

    def _read_source(self) -> dict:
        return self._read_customer() if self.mode == "customer" else self._read_internal()

    def _to_float(self, value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # -- discovery: find the correct data in an UNKNOWN customer source ----
    def discover(self) -> dict:
        """Introspect the customer's source so we can map target fields onto it.

        internal : list of {schema, table, column} the account can read.
        customer : the JSON keys/sample the customer's API returns.
        """
        return self._discover_customer() if self.mode == "customer" else self._discover_internal()

    def _discover_internal(self) -> dict:
        with self._database_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_schema, table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema, table_name, ordinal_position
                    """
                )
                rows = cur.fetchall()
        tables: dict = {}
        for schema, table, column, dtype in rows:
            key = f"{schema}.{table}"
            tables.setdefault(key, []).append({"column": column, "type": dtype})
        return {
            "mode": "internal",
            "tables": [{"table": k, "columns": v} for k, v in tables.items()],
        }

    def _discover_customer(self) -> dict:
        tables = self._fetch_tables()
        if tables:
            return {"mode": "customer", "tables": tables, "source": "metadata"}
        payload = self._fetch_json()
        return {"mode": "customer", "sample": payload, "fields": _flatten_keys(payload)}

    # -- full payload read: the mapping engine maps it (no field picked here) --
    def read_payload(self) -> Optional[dict]:
        """Latest record as a JSON-safe dict: the whole API response, or the whole newest row."""
        if self.mode == "customer":
            payload = self._fetch_json()
            return payload if isinstance(payload, dict) else {"items": payload}
        if not self.host or not self.database or not self.table:
            raise DriverError("Database source requires host, database and table.")
        order_col = self.order_by or self.mapping.get("timestamp")
        order_clause = f" ORDER BY {quote_ident(order_col)} DESC" if order_col else ""
        with self._database_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {quote_ident(self.table)}{order_clause} LIMIT 1")
                row = cur.fetchone()
                columns = [d[0] for d in cur.description] if cur.description else []
        if not row:
            return None
        return {col: _json_safe(val) for col, val in zip(columns, row)}

    # -- replay: walk through historical rows ------------------------------
    def _table_parts(self) -> tuple[str, str]:
        schema, table = "public", self.table or ""
        if "." in table:
            schema, table = table.split(".", 1)
        return schema, table

    def _replay_time_column(self) -> Optional[str]:
        """Configured time column, or the first timestamp-like column in the table."""
        if self.replay_time_column:
            return self.replay_time_column
        if self._time_column_cache is not None:
            return self._time_column_cache
        schema, table = self._table_parts()
        with self._database_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "AND data_type IN ('timestamp with time zone', 'timestamp without time zone', 'date') "
                    "ORDER BY ordinal_position LIMIT 1",
                    (schema, table),
                )
                row = cur.fetchone()
        self._time_column_cache = row[0] if row else None
        return self._time_column_cache

    def _replay_order_column(self) -> Optional[str]:
        return self.replay_order_by or self._replay_time_column()

    @staticmethod
    def _cursor_kind(cursor):
        """Return an int/ISO string cursor, or None when it is not reusable."""
        if cursor in (None, ""):
            return None
        text = str(cursor)
        try:
            return int(text)
        except (TypeError, ValueError):
            pass
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            return text
        except ValueError:
            return None

    def _replay_row(self, cursor, offset: int = 0) -> Optional[dict]:
        order_col = self._replay_order_column()
        if not order_col:
            raise DriverError("Replay requires an order_by or a timestamp column.")
        time_col = self._replay_time_column()

        where: list[str] = []
        params: list = []
        if time_col:
            if self.replay_from:
                where.append(f"{quote_ident(time_col)} >= %s::timestamptz")
                params.append(str(self.replay_from))
            if self.replay_to:
                where.append(f"{quote_ident(time_col)} <= %s::timestamptz")
                params.append(str(self.replay_to))
        if cursor is not None:
            if isinstance(cursor, int):
                where.append(f"{quote_ident(order_col)} > %s")
            else:
                where.append(f"{quote_ident(order_col)} > %s::timestamptz")
            params.append(cursor)

        sql = f"SELECT * FROM {quote_ident(self.table)}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {quote_ident(order_col)} ASC"
        if offset:
            sql += f" OFFSET {int(offset)}"
        sql += " LIMIT 1"

        with self._database_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                columns = [d[0] for d in cur.description] if cur.description else []
        if not row:
            return None
        return {col: _json_safe(val) for col, val in zip(columns, row)}

    def read_replay_payload(self, cursor: Optional[str] = None) -> Optional[tuple[dict, Optional[str]]]:
        """Return (next historical row, new cursor), looping when configured.

        The cursor is the value of the order column (id or timestamp) of the row
        just read. ``step`` jumps several rows per call so a long history can be
        replayed at a watchable speed.
        """
        if not self.replay:
            return None
        if not self.host or not self.database or not self.table:
            raise DriverError("Replay requires host, database and table.")

        normalised = self._cursor_kind(cursor)
        offset = 0 if normalised is None else self.replay_step - 1
        row = self._replay_row(normalised, offset)
        if row is None and self.replay_loop:
            row = self._replay_row(None, 0)
        if row is None:
            return None

        order_col = (self._replay_order_column() or "").split(".")[-1]
        next_cursor = row.get(order_col)
        if next_cursor is not None:
            next_cursor = str(next_cursor)

        # Optionally stamp the row with the current time so the dashboard graph
        # keeps moving forward while old values are being replayed.
        time_col = self._replay_time_column()
        if self.replay_timestamp == "now" and time_col:
            row[time_col] = datetime.now(timezone.utc).isoformat()

        return row, next_cursor

    # -- driver contract ---------------------------------------------------
    def checkConnection(self) -> bool:  # noqa: N802
        if self.simulate:
            return True
        try:
            return bool(self.read_payload())
        except Exception:
            return False

    def disconnect(self) -> None:
        pass  # stateless HTTP/SQL connector; nothing to release

    def readMeasurement(self) -> dict:  # noqa: N802
        ts = datetime.now(timezone.utc).isoformat()
        device_id = self.config.get("device_id")
        if self.simulate:
            raw = self._simulate_raw()
        else:
            try:
                data = self._read_source()
            except Exception:
                data = {}
            raw = self._to_float(data.get("measurement_raw"))
            if data.get("timestamp"):
                ts = str(data.get("timestamp"))
            if data.get("device_id"):
                device_id = data.get("device_id")
        return {
            "device_id": device_id,
            "timestamp": ts,
            "measurement_raw": raw,
            "communication_status": "online" if raw is not None else "offline",
        }

    # backward-compatible simple read used by generic callers
    def read(self) -> dict:
        return self.readMeasurement()

    # -- simulation --------------------------------------------------------
    def _simulate_raw(self) -> int:
        centre = float(self.config.get("simulate_displacement_mm", 1.5))
        t = time.time() - self._t0
        displacement = centre + 0.15 * math.sin(t / 6.0) + random.uniform(-0.02, 0.02)
        return int(round(displacement / 0.01))