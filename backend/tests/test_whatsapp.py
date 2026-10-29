import hashlib
import hmac
import json

from app.config import settings
from app.services.ai_assistant.channels import whatsapp


def test_whatsapp_signature_accepts_valid_meta_header(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "test-secret")
    raw = b'{"entry":[]}'
    signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()

    assert whatsapp.verify_signature(raw, signature) is True
    assert whatsapp.verify_signature(raw, "sha256=deadbeef") is False
    assert whatsapp.verify_signature(raw, None) is False


def test_whatsapp_signature_requires_explicit_unsigned_mode(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    monkeypatch.setattr(settings, "whatsapp_allow_unsigned", False)
    assert whatsapp.verify_signature(b"{}", None) is False

    monkeypatch.setattr(settings, "whatsapp_allow_unsigned", True)
    assert whatsapp.verify_signature(b"{}", None) is True


def test_whatsapp_parser_extracts_text_messages_only():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [
                        {"id": "wamid.1", "from": "919999999999", "type": "text",
                         "text": {"body": "  average thickness today  "}, "timestamp": "1"},
                        {"id": "wamid.2", "from": "919999999999", "type": "image"},
                    ]
                }
            }]
        }]
    }

    messages = whatsapp.parse_messages(payload)

    assert messages == [{
        "provider_message_id": "wamid.1",
        "from": "919999999999",
        "text": "average thickness today",
        "timestamp": "1",
    }]


def test_whatsapp_parser_ignores_status_only_payloads():
    assert whatsapp.parse_messages({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []


def test_whatsapp_link_message_is_recognised(monkeypatch):
    captured = {}

    monkeypatch.setattr(whatsapp, "link_identity", lambda code, external: (
        captured.update({"code": code, "external": external}) or
        {"company_id": "company-a", "user_id": "user-a"}))

    reply = whatsapp._handle_link("LINK ABCD1234", "919999999999")

    assert "linked" in reply.lower()
    assert captured == {"code": "ABCD1234", "external": "919999999999"}
