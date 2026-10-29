"""Outbox worker for WhatsApp replies.

Uses the existing psycopg2 pool and a daemon thread, so no Redis/Celery is
required. A failed send stays in core.ai_outbox with a retry time.
"""
from __future__ import annotations

import threading
from typing import Optional

from ...config import settings
from .channels import whatsapp

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
INTERVAL_S = 5.0


def _loop() -> None:
    while not _stop.is_set():
        try:
            whatsapp.flush_outbox(20)
        except Exception as exc:
            print(f"[ai-worker] outbox error: {exc}")
        _stop.wait(INTERVAL_S)


def start() -> None:
    global _thread
    if not settings.ai_enabled or not settings.ai_worker_enabled:
        return
    # Mock/disabled deliveries are flushed inline by the webhook/test route.
    if settings.whatsapp_driver != "graph":
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ai-outbox", daemon=True)
    _thread.start()
    print("[ai] outbox worker started")


def stop() -> None:
    _stop.set()
