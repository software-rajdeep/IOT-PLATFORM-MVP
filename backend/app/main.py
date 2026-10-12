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
from .routers import auth, catalog, demo_customer, devices, gateway, instances, meta


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.enable_simulator:
        from . import simulator

        simulator.start()
    yield
    if settings.enable_simulator:
        from . import simulator

        simulator.stop()


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

for module in (meta, auth, catalog, devices, instances, gateway, demo_customer):
    app.include_router(module.router)

# Serve the frontend last so /api/v1 routes take precedence.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")