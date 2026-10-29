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
2. CUSTOMER — the customer's own server, reachable only via API (URL + API key;
   an additional secret is optional). Discovery understands three shapes:
   - a `/tables` listing (our generic customer API),
   - a PostgREST/Neon Data API OpenAPI spec (`<api_url>/openapi.json`),
   - otherwise the JSON keys of the latest response.
   For a PostgREST endpoint, rows are read from `<api_url>/<table>` instead of a
   `?table=` query parameter.

Every connector returns the same customer-agnostic raw value dict:

    {
      "device_id": "DEV001",
      "timestamp": "2026-10-08T10:30:22.432+05:30",
      "measurement_raw": 151,
      "communication_status": "online"
    }
"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import math
import os
import queue
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from .base import DeviceDriver, DriverError
from .registry import register

DEFAULT_PORT = 5432
MAX_RESPONSE_BYTES = 1_000_000
# Several replay sources can poll the same database at once (one connection per poll).
# Keep this comfortably above the number of concurrent sources; managed poolers such
# as Neon accept far more than this.
POOL_MAX_CONNECTIONS = max(4, int(os.environ.get("DB_POOL_MAX", "32")))
POOL_WAIT_S = 0.05

# The target fields the platform can consume. Keys are TARGET field names.
TARGET_FIELDS = ("measurement_raw", "timestamp", "device_id")

DEFAULT_RAW_COLUMN = "measurement_raw"
_connection_pools = {}
_connection_pools_lock = threading.Lock()
_id_column_cache: dict[tuple, Optional[str]] = {}
_id_column_lock = threading.Lock()


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


class _SimpleConnectionPool:
    """Small connection pool that creates connections outside its lock.

    psycopg2's ThreadedConnectionPool holds one global lock while dialling a new
    managed-Postgres connection. With many replay sources, one slow Neon/TLS
    handshake then blocks every other poll for tens of seconds. Here connections
    are opened by the calling worker; the lock only guards the counter.
    """

    def __init__(self, maxconn: int, connect_kwargs: dict):
        self.maxconn = maxconn
        self.connect_kwargs = connect_kwargs
        self._idle: "queue.LifoQueue" = queue.LifoQueue(maxsize=maxconn)
        self._created = 0
        self._lock = threading.Lock()

    def getconn(self, timeout: float = 5.0):
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            create = self._created < self.maxconn
            if create:
                self._created += 1
        if create:
            try:
                import psycopg2
                return psycopg2.connect(**self.connect_kwargs)
            except Exception:
                with self._lock:
                    self._created = max(0, self._created - 1)
                raise
        try:
            return self._idle.get(timeout=timeout)
        except queue.Empty:
            from psycopg2.pool import PoolError
            raise PoolError("connection pool exhausted")

    def putconn(self, conn, close: bool = False, **_kwargs):
        if close:
            try:
                conn.close()
            except Exception:
                pass
            with self._lock:
                self._created = max(0, self._created - 1)
            return
        try:
            self._idle.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass
            with self._lock:
                self._created = max(0, self._created - 1)


# Query parameters that PostgREST adds to table paths; they are operations, not columns.
_POSTGREST_RESERVED = {
    "select", "order", "limit", "offset", "on_conflict", "columns", "and", "or", "not",
    "include", "prefer", "range", "range-unit",
}


def _schema_columns(schema: Any) -> list[dict]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return []
    columns: list[dict] = []
    for name, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        dtype = spec.get("format") or spec.get("type") or ""
        columns.append({"column": str(name), "type": str(dtype)})
    return columns


def _openapi_tables(spec: Any) -> list[dict]:
    """Tables/columns from a PostgREST/Neon Data API OpenAPI document."""
    if not isinstance(spec, dict):
        return []
    schemas = spec.get("definitions")
    if not isinstance(schemas, dict):
        components = spec.get("components")
        schemas = components.get("schemas") if isinstance(components, dict) else None
    tables: list[dict] = []
    for name, schema in (schemas or {}).items():
        columns = _schema_columns(schema)
        if columns:
            tables.append({"table": str(name), "columns": columns})
    if tables:
        return tables

    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("/rpc/"):
            continue
        table = path.strip("/")
        if not table or "{" in table:
            continue
        columns = []
        parameters = (item or {}).get("get", {}).get("parameters") if isinstance(item, dict) else None
        for param in parameters or []:
            if not isinstance(param, dict) or param.get("in") != "query":
                continue
            name = param.get("name")
            if not name or name in _POSTGREST_RESERVED:
                continue
            columns.append({"column": str(name), "type": str(param.get("format") or param.get("type") or "")})
        if columns:
            tables.append({"table": table, "columns": columns})
    return tables


def _tables_from_payload(data: Any) -> Optional[list]:
    if isinstance(data, dict):
        tables = data.get("tables")
        if isinstance(tables, list):
            return [t for t in tables if isinstance(t, dict) and t.get("columns")]
        return _openapi_tables(data) or None
    return None


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

        # optional JWT refresh (Neon Auth / Better Auth). The service account
        # signs in to mint a short-lived bearer token; the platform refreshes it.
        self.auth_url = db.get("auth_url") or self.config.get("auth_url")
        self.auth_email = db.get("auth_email") or self.config.get("auth_email")
        self.auth_password = db.get("auth_password") or self.config.get("auth_password")
        self.auth_origin = db.get("auth_origin") or self.config.get("auth_origin")
        # Neon Data API: its auth service sits next to it, so API URL + API key
        # (a session token) is enough; the token refresh URL is derived.
        self._derived_auth_urls: list = []
        if not self.auth_url and self.api_key and not self._looks_like_jwt(self.api_key):
            self._derived_auth_urls = self._neon_auth_urls(self.api_url)
            if self._derived_auth_urls:
                self.auth_url = self._derived_auth_urls[0]
        self._jwt: Optional[str] = None
        self._jwt_expires_at = 0.0

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
        self.replay_order_by = replay.get("order_by")
        # Optional: only replay rows whose sensor value actually changed, so long
        # constant stretches in historical data do not look like a frozen dashboard.
        self.replay_distinct_column = replay.get("distinct_column")
        self.replay_valid_min = replay.get("valid_min")
        self.replay_valid_max = replay.get("valid_max")
        self._time_column_cache = None

        self.timeout = float(self.config.get("timeout", 15))
        self.simulate = bool(self.config.get("simulate", not self._configured()))
        self._t0 = time.time()

    # -- mapping helpers ---------------------------------------------------
    @property
    def raw_source(self) -> str:
        """The customer source mapped to measurement_raw (required target)."""
        return self.mapping.get("measurement_raw") or DEFAULT_RAW_COLUMN

    def _configured(self) -> bool:
        if self.mode == "customer":
            refresh = self.auth_url and self.auth_email and self.auth_password
            return bool(self.api_url and (self.api_key or self.api_secret or refresh))
        return bool(self.host and self.database and self.table)

    # ---- internal mode: direct database ---------------------------------
    def _connect(self):
        try:
            import psycopg2  # noqa: F401
            from psycopg2.pool import PoolError
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
                pool = _SimpleConnectionPool(POOL_MAX_CONNECTIONS, kwargs)
                _connection_pools[key] = pool
        try:
            return pool, pool.getconn(timeout=max(self.timeout, 1.0))
        except PoolError as exc:
            raise DriverError(
                "Source database is busy (connection pool exhausted). "
                "Lower the poll rate or disable some replay sources."
            ) from exc

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

    def _url_with_path(self, url: str, suffix: str) -> str:
        parts = urllib.parse.urlsplit(url or "")
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + suffix, "", "")
        )

    def _is_postgrest(self) -> bool:
        parts = urllib.parse.urlsplit(self.api_url or "")
        return parts.path.rstrip("/").endswith("/rest/v1")

    def _postgrest_table(self) -> tuple[str, Optional[str]]:
        relation = self.table or ""
        schema = None
        if "." in relation:
            schema, relation = relation.split(".", 1)
        return relation, schema

    def _postgrest_url(self) -> str:
        relation, _schema = self._postgrest_table()
        if not relation:
            raise DriverError("Select a table exposed by this API before reading.")
        parts = urllib.parse.urlsplit(self.api_url or "")
        query = {"limit": "1"}
        order = self.order_by or self.mapping.get("timestamp")
        if order:
            query["order"] = f"{order}.desc"
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/" + urllib.parse.quote(relation),
             urllib.parse.urlencode(query), parts.fragment)
        )

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

    def _auth_origin(self) -> str:
        if self.auth_origin:
            return self.auth_origin.rstrip("/")
        from ..config import settings
        base = (settings.public_base_url or "").rstrip("/")
        if not base or "127.0.0.1" in base or "0.0.0.0" in base:
            host = settings.app_host if settings.app_host not in ("", "0.0.0.0", "127.0.0.1") else "localhost"
            base = f"http://{host}:{settings.app_port}"
        return base

    @staticmethod
    def _jwt_expiry(token: str) -> Optional[float]:
        try:
            part = token.split(".")[1]
            part += "=" * (-len(part) % 4)
            claims = json.loads(base64.urlsafe_b64decode(part).decode("utf-8"))
            return float(claims["exp"])
        except Exception:
            return None

    @staticmethod
    def _looks_like_jwt(token: str) -> bool:
        return bool(token) and token.startswith("eyJ") and token.count(".") == 2

    @classmethod
    def _jwt_from_response(cls, response) -> Optional[str]:
        """Read a JWT from ``set-auth-jwt`` or a ``{"token": "..."}`` body.

        Neon Auth returns the short-lived JWT from ``/token`` in the JSON body;
        older Better Auth builds put it in the ``set-auth-jwt`` response header.
        """
        header = response.headers.get("set-auth-jwt")
        body = response.read()
        if header:
            return header
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, dict):
            for value in (data.get("token"), (data.get("session") or {}).get("token")):
                if isinstance(value, str) and cls._looks_like_jwt(value):
                    return value
        return None

    @staticmethod
    def _neon_auth_urls(api_url: Optional[str]) -> list:
        """Neon Auth URLs implied by a Neon Data API URL (`.apirest.` -> `.neonauth.`)."""
        parts = urllib.parse.urlsplit(api_url or "")
        host = parts.hostname or ""
        if ".apirest." not in host or not host.endswith(".neon.tech"):
            return []
        auth_host = host.replace(".apirest.", ".neonauth.", 1)
        database = parts.path.strip("/").split("/")[0]
        names = dict.fromkeys(n for n in ("neondb", database) if n)
        return [f"https://{auth_host}/{name}/auth" for name in names]

    def _refresh_session_jwt(self) -> str:
        """Exchange a long-lived session token (the API key) for a short-lived JWT."""
        candidates = self._derived_auth_urls or [self.auth_url]
        error: Optional[DriverError] = None
        for url in candidates:
            self.auth_url = url
            try:
                token = self._refresh_session_jwt_at()
                self._derived_auth_urls = [url] if self._derived_auth_urls else []
                return token
            except DriverError as exc:
                error = exc
        raise error

    def _refresh_session_jwt_at(self) -> str:
        if not (self.auth_url and self.api_key):
            raise DriverError("Session-token refresh requires auth_url and api_key.")
        from ..services.source_guard import SourceBlocked, check_url
        auth_url = self.auth_url.rstrip("/")
        try:
            check_url(auth_url)
        except SourceBlocked as exc:
            raise DriverError(str(exc)) from exc
        origin = self._auth_origin()
        first_error: Optional[Exception] = None
        session_token = self.api_key or ""
        if "=" in session_token and session_token.count(".") <= 2:
            name, value = session_token.split("=", 1)
            if name.startswith(("__Secure-", "better-auth", "neon-auth", "neonauth")):
                session_token = value
        # Managed Better Auth (Neon) names its cookie `__Secure-neonauth.session_token`;
        # older/self-hosted Better Auth deployments use the other names. Send them all
        # in one request so a token refresh never trips the auth rate limit.
        cookie_names = (
            "__Secure-neonauth.session_token",
            "neonauth.session_token",
            "__Secure-neon-auth.session_token",
            "neon-auth.session_token",
            "better-auth.session_token",
            "__Secure-better-auth.session_token",
        )
        headers = {
            "Accept": "application/json",
            "Origin": origin,
            "Cookie": "; ".join(f"{name}={session_token}" for name in cookie_names),
        }
        # Neon Auth mints the Data API JWT at /token; /get-session is the fallback
        # for deployments that only expose the set-auth-jwt header.
        for path in ("/token", "/get-session"):
            req = urllib.request.Request(auth_url + path, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
                    token = self._jwt_from_response(response)
            except urllib.error.HTTPError as exc:
                first_error = first_error or exc
                try:
                    exc.read()
                except Exception:
                    pass
                continue
            except Exception as exc:
                first_error = first_error or exc
                continue
            if token:
                self._jwt = token
                self._jwt_expires_at = self._jwt_expiry(token) or (time.time() + 300)
                return token
        if isinstance(first_error, urllib.error.HTTPError):
            if first_error.code == 429:
                raise DriverError(
                    "Auth service rate-limited the token refresh (HTTP 429). "
                    "Retry shortly, or paste a short-lived JWT as the API key."
                ) from first_error
            if first_error.code in (401, 403):
                raise DriverError(
                    "Auth service rejected the API key (session token) "
                    f"(HTTP {first_error.code}). Paste a fresh session token or a JWT."
                ) from first_error
            raise DriverError(
                f"Auth service did not accept the API key (session token). HTTP {first_error.code}."
            ) from first_error
        raise DriverError(
            "Auth service found no session for this API key. Paste a session token from the "
            "same Neon Auth project, or a short-lived JWT instead."
        )

    def _refresh_jwt(self) -> str:
        """Sign in to the auth service and return a fresh JWT bearer token."""
        if not (self.auth_url and self.auth_email and self.auth_password):
            raise DriverError("JWT refresh requires auth_url, auth_email and auth_password.")
        from ..services.source_guard import SourceBlocked, check_url
        auth_url = self.auth_url.rstrip("/")
        try:
            check_url(auth_url)
        except SourceBlocked as exc:
            raise DriverError(str(exc)) from exc
        origin = self._auth_origin()
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def request(path: str, data: Optional[bytes] = None) -> Optional[str]:
            headers = {"Accept": "application/json", "Origin": origin}
            if data is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                auth_url + path, data=data, method="POST" if data is not None else "GET", headers=headers
            )
            try:
                with opener.open(req, timeout=self.timeout) as response:  # noqa: S310
                    return self._jwt_from_response(response)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read(2048).decode("utf-8", "replace").strip()
                except Exception:
                    pass
                message = f"Auth sign-in failed: HTTP {exc.code}"
                raise DriverError(f"{message}: {detail}" if detail else message) from exc
            except Exception as exc:
                raise DriverError(f"Auth sign-in failed: {exc}") from exc

        payload = json.dumps({"email": self.auth_email, "password": self.auth_password}).encode()
        # sign-in sets the session cookie; /token then returns the Data API JWT
        # (the sign-in body's own `token` is the session token, not a JWT).
        token = request("/sign-in/email", payload)
        if not token:
            token = request("/token")
        if not token:
            token = request("/get-session")
        if not token:
            raise DriverError("Auth service did not return a JWT (set-auth-jwt or /token).")
        self._jwt = token
        self._jwt_expires_at = self._jwt_expiry(token) or (time.time() + 300)
        return token

    def _credential(self) -> Optional[str]:
        refresh = (self.auth_url and self.api_key and not self._looks_like_jwt(self.api_key)) or (
            self.auth_url and self.auth_email and self.auth_password
        )
        if refresh:
            if not self._jwt or time.time() >= self._jwt_expires_at - 60:
                if self.auth_email and self.auth_password:
                    return self._refresh_jwt()
                return self._refresh_session_jwt()
            return self._jwt
        return self.api_secret or self.api_key

    def _request_json(self, url: str, extra_headers: Optional[dict] = None) -> Any:
        credential = self._credential()
        if not (url and credential):
            raise DriverError("Customer API connection requires api_url and an API key/secret or auth credentials.")
        from ..services.source_guard import SourceBlocked, check_url
        try:
            check_url(url)
        except SourceBlocked as exc:
            raise DriverError(str(exc)) from exc
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {credential}",
        }
        if self.api_key and not self.auth_url:
            headers["X-Api-Key"] = self.api_key
        if self.api_secret and not self.auth_url:
            headers["X-Api-Secret"] = self.api_secret
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(2048).decode("utf-8", "replace").strip()
            except Exception:
                pass
            message = f"Customer API request failed: HTTP {exc.code}"
            raise DriverError(f"{message}: {detail}" if detail else message) from exc
        except Exception as exc:
            raise DriverError(f"Customer API request failed: {exc}") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise DriverError("Customer API response is larger than 1 MB")
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise DriverError("Customer API did not return JSON") from exc

    def _fetch_json(self) -> Any:
        if self._is_postgrest():
            _relation, schema = self._postgrest_table()
            headers = {"Accept-Profile": schema} if schema else None
            data = self._request_json(self._postgrest_url(), headers)
            if isinstance(data, list):
                return data[0] if data else None
            return data
        return self._request_json(self._with_table(self.api_url))

    def _fetch_tables(self) -> Optional[list]:
        """Ask the API for a table listing. None when unsupported.

        Tries the OpenAPI document (PostgREST/Neon Data API) and the `/tables`
        listing of our generic customer API. For a PostgREST endpoint, the first
        error (usually an auth/JWT problem) is surfaced instead of being hidden.
        """
        candidates = (
            self._url_with_path(self.api_url, "/openapi.json"),
            self._url_with_path(self.api_url, "/tables"),
            f"{self._api_base()}/tables",
        )
        first_error: Optional[Exception] = None
        for url in candidates:
            try:
                data = self._request_json(url)
            except Exception as exc:
                first_error = first_error or exc
                continue
            tables = _tables_from_payload(data)
            if tables:
                return tables
        if self._is_postgrest() and first_error:
            raise first_error
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
        if self.replay_order_by:
            return self.replay_order_by
        # Prefer an integer key (usually the primary key) so replay advances with an
        # index scan. Ordering by a non-indexed timestamp column scans the whole
        # history on every poll, which makes a busy dashboard crawl.
        return self._replay_id_column() or self._replay_time_column() or self.order_by

    def _replay_id_column(self) -> Optional[str]:
        key = (self.host, self.port, self.database, self.table)
        with _id_column_lock:
            if key in _id_column_cache:
                return _id_column_cache[key]
        schema, table = self._table_parts()
        with self._database_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "AND data_type IN ('bigint', 'integer', 'smallint') "
                    "ORDER BY (column_name = 'id') DESC, ordinal_position LIMIT 1",
                    (schema, table),
                )
                row = cur.fetchone()
        value = row[0] if row else None
        with _id_column_lock:
            _id_column_cache[key] = value
        return value

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

        distinct_col = self.replay_distinct_column
        if distinct_col:
            col = quote_ident(distinct_col)
            where.append(f"{col} IS NOT NULL")
            if self.replay_valid_min is not None:
                where.append(f"{col} >= %s")
                params.append(float(self.replay_valid_min))
            if self.replay_valid_max is not None:
                where.append(f"{col} <= %s")
                params.append(float(self.replay_valid_max))
            if cursor is not None:
                cursor_cast = "" if isinstance(cursor, int) else "::timestamptz"
                where.append(
                    f"{col} IS DISTINCT FROM (SELECT {col} FROM {quote_ident(self.table)} "
                    f"WHERE {quote_ident(order_col)} = %s{cursor_cast})"
                )
                params.append(cursor)

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