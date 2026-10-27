import base64
import io
import json
import time
import urllib.error
import urllib.request

import pytest

from app.drivers.base import DriverError
from app.drivers.database_api import DatabaseApiConnector


class _FakeResponse:
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self, _n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _capture(monkeypatch, payload=None):
    captured = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["url"] = req.full_url
        return _FakeResponse(json.dumps(payload or {"value": 12}).encode())

    monkeypatch.setattr("app.services.source_guard.check_url", lambda url: None)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_api_key_only_is_a_valid_customer_connection(monkeypatch):
    captured = _capture(monkeypatch)
    driver = DatabaseApiConnector({
        "database": {"mode": "customer", "api_url": "https://customer.example.com/api/latest", "api_key": "key-1"}
    })

    assert driver._configured() is True
    assert driver._request_json(driver.api_url) == {"value": 12}
    assert captured["headers"]["authorization"] == "Bearer key-1"
    assert captured["headers"]["x-api-key"] == "key-1"
    assert "x-api-secret" not in captured["headers"]


def test_api_key_and_secret_both_sent(monkeypatch):
    captured = _capture(monkeypatch)
    driver = DatabaseApiConnector({
        "database": {
            "mode": "customer",
            "api_url": "https://customer.example.com/api/latest",
            "api_key": "key-1",
            "api_secret": "secret-1",
        }
    })

    driver._request_json(driver.api_url)
    assert captured["headers"]["authorization"] == "Bearer secret-1"
    assert captured["headers"]["x-api-key"] == "key-1"
    assert captured["headers"]["x-api-secret"] == "secret-1"


def test_customer_mode_requires_a_credential(monkeypatch):
    _capture(monkeypatch)
    driver = DatabaseApiConnector({
        "database": {"mode": "customer", "api_url": "https://customer.example.com/api/latest"}
    })

    assert driver._configured() is False
    with pytest.raises(DriverError):
        driver._request_json(driver.api_url)


def test_http_error_includes_the_api_response_body(monkeypatch):
    _capture(monkeypatch)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        body = io.BytesIO(b'{"message":"Provided authentication token is not a valid JWT encoding"}')
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    driver = DatabaseApiConnector({
        "database": {"mode": "customer", "api_url": "https://customer.example.com/api/latest", "api_key": "key-1"}
    })

    with pytest.raises(DriverError) as exc:
        driver._request_json(driver.api_url)
    assert "HTTP 400" in str(exc.value)
    assert "valid JWT" in str(exc.value)


def test_table_is_appended_to_the_api_url(monkeypatch):
    _capture(monkeypatch)
    driver = DatabaseApiConnector({
        "database": {
            "mode": "customer",
            "api_url": "https://customer.example.com/api/latest?line=1",
            "api_key": "key-1",
            "table": "public.rubber",
        }
    })

    assert driver._with_table(driver.api_url) == (
        "https://customer.example.com/api/latest?line=1&table=public.rubber"
    )


def test_replay_settings_are_parsed():
    driver = DatabaseApiConnector({
        "database": {
            "mode": "internal",
            "host": "db.example.com",
            "database": "customer",
            "table": "public.readings",
            "replay": {
                "enabled": True,
                "from": "2026-10-16T07:16:00Z",
                "to": "2026-10-17T07:12:00Z",
                "step": 25,
                "loop": False,
                "timestamp": "source",
            },
        }
    })

    assert driver.replay is True
    assert driver.replay_from == "2026-10-16T07:16:00Z"
    assert driver.replay_to == "2026-10-17T07:12:00Z"
    assert driver.replay_step == 25
    assert driver.replay_loop is False
    assert driver.replay_timestamp == "source"


def test_replay_accepts_a_boolean_shorthand():
    driver = DatabaseApiConnector({
        "database": {"mode": "internal", "host": "h", "database": "d", "table": "t", "replay": True}
    })

    assert driver.replay is True
    assert driver.replay_step == 1
    assert driver.replay_loop is True
    assert driver.replay_timestamp == "now"


def _postgrest_driver(**extra):
    return DatabaseApiConnector({
        "database": {
            "mode": "customer",
            "api_url": "https://project.apirest.us-east-1.aws.neon.tech/neondb/rest/v1",
            "api_key": "jwt-token",
            **extra,
        }
    })


def test_postgrest_openapi_discovery_lists_tables(monkeypatch):
    spec = {
        "swagger": "2.0",
        "definitions": {
            "rubber": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "format": "bigint"},
                    "timestamp": {"type": "string", "format": "timestamp with time zone"},
                    "sensor_a": {"type": "number", "format": "real"},
                },
            }
        },
    }
    driver = _postgrest_driver()
    seen = {}

    def fake_request(url, extra_headers=None):  # noqa: ARG001
        seen["url"] = url
        return spec

    monkeypatch.setattr(driver, "_request_json", fake_request)

    assert driver._fetch_tables() == [{
        "table": "rubber",
        "columns": [
            {"column": "id", "type": "bigint"},
            {"column": "timestamp", "type": "timestamp with time zone"},
            {"column": "sensor_a", "type": "real"},
        ],
    }]
    assert seen["url"].endswith("/neondb/rest/v1/openapi.json")


def test_postgrest_read_uses_the_table_path_and_unwraps_the_row(monkeypatch):
    driver = _postgrest_driver(table="public.rubber", order_by="id")
    seen = {}

    def fake_request(url, extra_headers=None):
        seen["url"] = url
        seen["headers"] = extra_headers
        return [{"id": 7, "sensor_a": 1.23}]

    monkeypatch.setattr(driver, "_request_json", fake_request)

    assert driver._fetch_json() == {"id": 7, "sensor_a": 1.23}
    assert seen["url"] == (
        "https://project.apirest.us-east-1.aws.neon.tech/neondb/rest/v1/rubber?limit=1&order=id.desc"
    )
    assert seen["headers"] == {"Accept-Profile": "public"}


def test_postgrest_discovery_surfaces_the_auth_error(monkeypatch):
    driver = _postgrest_driver()
    error = DriverError(
        'Customer API request failed: HTTP 400: {"message":"Provided authentication token is not a valid JWT encoding"}'
    )

    def fake_request(url, extra_headers=None):  # noqa: ARG001
        raise error

    monkeypatch.setattr(driver, "_request_json", fake_request)

    with pytest.raises(DriverError) as exc:
        driver._fetch_tables()
    assert "valid JWT" in str(exc.value)


def _jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def test_jwt_refresh_signs_in_and_caches_the_token(monkeypatch):
    token = _jwt(time.time() + 3600)
    calls = []

    class FakeResponse:
        def __init__(self, jwt):
            self.headers = {"set-auth-jwt": jwt} if jwt else {}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class FakeOpener:
        def open(self, req, timeout=None):  # noqa: ARG002
            calls.append(req.full_url)
            return FakeResponse(token if req.full_url.endswith("/sign-in/email") else None)

    monkeypatch.setattr("app.services.source_guard.check_url", lambda url: None)  # noqa: ARG005
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: FakeOpener())
    driver = DatabaseApiConnector({
        "database": {
            "mode": "customer",
            "api_url": "https://project.apirest.us-east-1.aws.neon.tech/neondb/rest/v1",
            "auth_url": "https://project.neonauth.us-east-1.aws.neon.tech/neondb/auth",
            "auth_email": "source@example.com",
            "auth_password": "pw",
        }
    })

    assert driver._configured() is True
    assert driver._credential() == token
    assert driver._credential() == token
    assert calls == ["https://project.neonauth.us-east-1.aws.neon.tech/neondb/auth/sign-in/email"]


def test_session_token_api_key_is_exchanged_for_a_jwt(monkeypatch):
    token = _jwt(time.time() + 3600)
    calls = []

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        calls.append({"url": req.full_url, "headers": {k.lower(): v for k, v in req.header_items()}})
        return _FakeResponse(b'{"session":{}}', headers={"set-auth-jwt": token})

    monkeypatch.setattr("app.services.source_guard.check_url", lambda url: None)  # noqa: ARG005
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    driver = DatabaseApiConnector({
        "database": {
            "mode": "customer",
            "api_url": "https://project.apirest.us-east-1.aws.neon.tech/neondb/rest/v1",
            "api_key": "session-token",
            "auth_url": "https://project.neonauth.us-east-1.aws.neon.tech/neondb/auth",
        }
    })

    assert driver._credential() == token
    assert calls[0]["url"].endswith("/get-session")
    assert calls[0]["headers"]["cookie"] == "__Secure-neon-auth.session_token=session-token"
    assert driver._credential() == token
    assert len(calls) == 1


def test_auth_service_token_is_sent_as_bearer(monkeypatch):
    captured = _capture(monkeypatch)
    driver = DatabaseApiConnector({
        "database": {
            "mode": "customer",
            "api_url": "https://customer.example.com/api/latest",
            "auth_url": "https://auth.example.com/neondb/auth",
            "auth_email": "source@example.com",
            "auth_password": "pw",
        }
    })
    driver._jwt = "jwt-token"
    driver._jwt_expires_at = time.time() + 3600

    driver._request_json(driver.api_url)

    assert captured["headers"]["authorization"] == "Bearer jwt-token"
    assert "x-api-key" not in captured["headers"]
    assert "x-api-secret" not in captured["headers"]
