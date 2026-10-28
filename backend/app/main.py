"""FastAPI application entrypoint.

Run from the backend/ directory:
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

Serves the JSON API under /api/v1 and the dependency-free frontend at /.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR, settings
from .routers import auth, catalog, demo_customer, devices, email_alerts, gateway, instance_setup, instances, meta
from .services.dashboard import invalidate_dashboards


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auth_secret == "dev-secret-change-me":
        print("[security] AUTH_SECRET is the development default; set a real secret before any deployment.")
    from . import simulator
    from .routers import demo_customer as demo

    if settings.enable_poller:
        simulator.start()
    if settings.enable_demo_customer:
        demo.start_feeder()
    yield
    simulator.stop()
    demo.stop_feeder()


app = FastAPI(
    title="Universal Industrial Platform API",
    version="1.0.0",
    description="Data-driven industrial platform: devices, purposes, templates, telemetry.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def static_cache_policy(request, call_next):
    """Revalidate static assets instead of re-downloading them, and drop the
    dashboard cache whenever anything is written."""
    response = await call_next(request)
    path = request.url.path

    if request.method not in ("GET", "HEAD") and path.startswith("/api/"):
        invalidate_dashboards()

    if path == "/sw.js":
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
    elif path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith(("/js/", "/css/", "/vendor/")):
        response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    return response

for module in (meta, auth, catalog, devices, instances, instance_setup, gateway, demo_customer, email_alerts):
    app.include_router(module.router)
app.include_router(gateway.push_router)
app.include_router(email_alerts.legacy_router)

# Serve the frontend last so /api/v1 routes take precedence.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")