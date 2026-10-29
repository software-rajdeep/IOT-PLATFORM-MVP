"""Vercel Python entrypoint.

Exposes the FastAPI ASGI app from ``backend/app`` as ``app`` so Vercel's
Python runtime can serve it. Requests keep their original path (e.g.
``/api/v1/auth/login``) because of the rewrite in ``vercel.json``.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.main import app  # noqa: E402,F401
