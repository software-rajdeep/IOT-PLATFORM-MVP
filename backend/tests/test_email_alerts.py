from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import settings
from app.services import email_alerts


def _use_tmp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(email_alerts, "_DATA_DIR", tmp_path)


def test_config_roundtrip(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
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


def test_auth_url_carries_state_and_redirect(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
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


def test_send_without_token_reports_reauthentication(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "google_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "client-secret")
    email_alerts.update_config("company-a", {"enabled": True, "recipient_email": "a@example.com"})

    success, error = email_alerts.send_test("company-a")

    assert success is False
    assert "Sign in with Google" in error
