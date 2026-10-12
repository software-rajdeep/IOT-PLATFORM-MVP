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
from .routers import auth, catalog, demo_customer, devices, gateway, instance_setup, instances, meta


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
async def no_cache_static(request, call_next):
    """Never cache the dependency-free frontend so UI changes appear instantly."""
    response = await call_next(request)
    if request.url.path in ("/", "/index.html") or request.url.path.startswith(("/js/", "/css/")):
        response.headers["Cache-Control"] = "no-store"
    return response

for module in (meta, auth, catalog, devices, instances, instance_setup, gateway, demo_customer):
    app.include_router(module.router)
app.include_router(gateway.push_router)

# Serve the frontend last so /api/v1 routes take precedence.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")