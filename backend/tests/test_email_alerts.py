from urllib.parse import parse_qs, urlparse

import pytest

from app.config import settings
from app.services import email_alerts


@pytest.fixture
def store(monkeypatch):
    """In-memory replacement for the DB-backed storage helpers."""
    configs: dict[str, dict] = {}
    tokens: dict[str, dict] = {}
    states: dict[str, dict] = {}

    monkeypatch.setattr(email_alerts, "_read_config", lambda cid: configs.get(str(cid)))
    monkeypatch.setattr(email_alerts, "_write_config", lambda cid, cfg: configs.__setitem__(str(cid), cfg))
    monkeypatch.setattr(email_alerts, "_read_token", lambda cid: tokens.get(str(cid)))
    monkeypatch.setattr(email_alerts, "_write_token", lambda cid, tok: tokens.__setitem__(str(cid), tok))
    monkeypatch.setattr(
        email_alerts,
        "_store_oauth_state",
        lambda state, cid, verifier: states.__setitem__(
            state, {"company_id": str(cid), "code_verifier": verifier}
        ),
    )
    monkeypatch.setattr(email_alerts, "_take_oauth_state", lambda state: states.pop(state, None))
    return {"configs": configs, "tokens": tokens, "states": states}


def test_config_roundtrip(store):
    email_alerts.update_config(
        "company-a",
        {
            "enabled": True,
            "recipient_email": " alerts@example.com ",
            "alerts": {"offline": False},
            "cooldown": {"minutes": 12},
        },
    )
    config = email_alerts.public_config("company-a")
    assert config["enabled"] is True
    assert config["recipient_email"] == "alerts@example.com"
    assert config["alerts"]["offline"] is False
    assert config["alerts"]["fail_streak"] is True
    assert config["cooldown"]["minutes"] == 12


def test_auth_url_carries_state_and_redirect(store, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "http://localhost:8000/callback")

    url, state = email_alerts.create_auth_url("company-a")
    query = parse_qs(urlparse(url).query)

    assert "accounts.google.com" in url
    assert "gmail.send" in url
    assert "localhost%3A8000%2Fcallback" in url
    assert state.startswith("company-a:")
    assert query["state"][0] == state
    # The PKCE verifier is persisted so any instance can finish the exchange.
    saved = store["states"][state]
    assert saved["company_id"] == "company-a"
    assert saved["code_verifier"]


def test_oauth_callback_rejects_unknown_state(store, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")

    with pytest.raises(RuntimeError, match="expired|already used"):
        email_alerts.handle_oauth_callback("http://localhost/callback?code=x", "company-a:missing")


def test_send_without_token_reports_reauthentication(store, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    email_alerts.update_config("company-a", {"enabled": True, "recipient_email": "a@example.com"})

    success, error = email_alerts.send_test("company-a")

    assert success is False
    assert "Sign in with Google" in error
