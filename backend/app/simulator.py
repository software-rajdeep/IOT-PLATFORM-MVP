"""Source runtime (grew out of the built-in connector simulator).

Polls every active device on its own interval and pushes the raw payload through
the processing pipeline — the same path a live gateway uses:

    RS-485 CD22, simulate=true   -> the REAL connector in simulation mode (material tracks the target)
    RS-485 CD22, real port       -> the connector over serial or TCP (serial-to-Ethernet)
    database_api (API or DB)     -> the whole latest record, deduplicated, mapped by the mapping engine
    http_push                    -> nothing to poll (POST /api/v1/ingest/{device_id})

Failures are recorded on core.device_connections.last_error; offline alerts are
checked every few seconds. Disable with ENABLE_POLLER=0 (ENABLE_SIMULATOR=0 skips
simulated devices only).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .config import settings
from .db import execute, fetch_all
from .drivers import get_driver_class
from .services import alerts, incidents
from .services.devices import connector_config, connector_driver_key, load_device
from .services.instances import instances_for_device, load_config
from .services.processing import ingest_raw

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="source")
_in_flight: set[str] = set()
_next_due: dict[str, float] = {}
_last_hash: dict[str, str] = {}
_last_error: dict[str, Optional[str]] = {}
_sources_cache: list[dict] = []
_sources_cache_at = 0.0
_offline_in_flight = False
_t0 = time.time()
MIN_INTERVAL_S = 0.5
SOURCE_REFRESH_S = 1.0
OFFLINE_CHECK_S = 10.0


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _active_sources() -> list[dict]:
    return fetch_all(
        "SELECT d.device_id, c.interface, c.poll_interval_ms, c.connection_parameters FROM core.devices d "
        "LEFT JOIN core.device_connections c ON c.device_id = d.device_id "
        "WHERE d.status = 'active' AND coalesce(lower(c.interface), 'rs485') <> 'http_push'"
    )


def _centre_displacement(device_id: str) -> Optional[float]:
    """A displacement that (under the instance's calibration) yields its target.

    Makes the simulated material track the configured target thickness, so the
    demo shows realistic PASS/FAIL around the tolerance band.
    """
    for inst in instances_for_device(device_id):
        config = load_config(inst["instance_id"])
        try:
            scale = float(config.get("calibration.scale"))
            offset = float(config.get("calibration.offset"))
            target = float(config.get("thickness.target_mm"))
        except (TypeError, ValueError):
            continue
        if abs(scale) > 1e-9:
            return (target - offset) / scale
    return None


def _record_error(device_id: str, message: Optional[str]) -> None:
    if message is not None and _last_error.get(device_id) == message:
        return
    if message is None and device_id in _last_error and _last_error.get(device_id) is None:
        return
    _last_error[device_id] = message
    if message:
        execute("UPDATE core.device_connections SET last_error = %s, last_error_at = now() WHERE device_id = %s",
                (message[:500], device_id))
    else:
        execute("UPDATE core.device_connections SET last_error = NULL WHERE device_id = %s AND last_error IS NOT NULL",
                (device_id,))


def poll_once(device_id: str) -> None:
    device = load_device(device_id)
    if not device:
        return
    connector_cls = get_driver_class(connector_driver_key(device))
    if not connector_cls:
        return
    config = connector_config(device)
    connector = connector_cls(config, {"centre_distance": device.get("centre_distance")})
    try:
        if connector.simulate:
            centre = _centre_displacement(device_id)
            t = time.time() - _t0
            config["simulate_displacement_mm"] = (centre if centre is not None else 1.5) + 0.12 * math.sin(t / 6.0)
            connector = connector_cls(config, {"centre_distance": device.get("centre_distance")})
            payload = {"measurement_raw": connector.readMeasurement().get("measurement_raw")}
        elif getattr(connector, "replay", False) and hasattr(connector, "read_replay_payload"):
            result = connector.read_replay_payload(device.get("last_cursor"))
            if not result:
                _record_error(device_id, "Replay source has no rows")
                return
            payload, next_cursor = result
            ingest_raw(device_id, payload)
            if next_cursor is not None:
                execute("UPDATE core.device_connections SET last_cursor = %s WHERE device_id = %s",
                        (next_cursor, device_id))
            _record_error(device_id, None)
            return
        elif hasattr(connector, "read_payload"):
            payload = connector.read_payload()
            if not payload:
                _record_error(device_id, "Source returned no record")
                return
            digest = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
            if _last_hash.get(device_id) == digest:
                return  # same record as last poll: nothing new
            _last_hash[device_id] = digest
            execute("UPDATE core.device_connections SET last_cursor = %s WHERE device_id = %s", (digest, device_id))
        else:
            reading = connector.readMeasurement()
            if reading.get("measurement_raw") is None:
                _record_error(device_id, "No response from sensor (check wiring, address and port)")
                return
            payload = {"measurement_raw": reading["measurement_raw"]}
        ingest_raw(device_id, payload)
        _record_error(device_id, None)
    except Exception as exc:
        _record_error(device_id, str(exc))
    finally:
        try:
            connector.disconnect()
        except Exception:
            pass


def _run(device_id: str) -> None:
    try:
        poll_once(device_id)
    finally:
        _in_flight.discard(device_id)


def _check_offline() -> None:
    global _offline_in_flight
    try:
        alerts.check_offline()
    except Exception as exc:
        print(f"[sources] offline check error: {exc}")
    try:
        incidents.check_connectivity()
    except Exception as exc:
        print(f"[sources] connectivity incident check error: {exc}")
    finally:
        _offline_in_flight = False


def _loop() -> None:
    global _sources_cache_at, _offline_in_flight
    last_offline_check = 0.0
    while not _stop.is_set():
        now = time.monotonic()
        try:
            if now - _sources_cache_at >= SOURCE_REFRESH_S:
                _sources_cache[:] = _active_sources()
                _sources_cache_at = now
            for src in _sources_cache:
                params = _as_dict(src.get("connection_parameters"))
                simulated = params.get("simulate", True)
                if simulated and not settings.enable_simulator:
                    continue
                device_id = src["device_id"]
                if device_id in _in_flight or now < _next_due.get(device_id, 0):
                    continue
                interval = max(MIN_INTERVAL_S, (src.get("poll_interval_ms") or 1000) / 1000.0)
                _next_due[device_id] = now + interval
                _in_flight.add(device_id)
                _pool.submit(_run, device_id)
            if now - last_offline_check > OFFLINE_CHECK_S and not _offline_in_flight:
                last_offline_check = now
                _offline_in_flight = True
                threading.Thread(target=_check_offline, name="offline-check", daemon=True).start()
        except Exception as exc:
            print(f"[sources] loop error: {exc}")
        _stop.wait(0.1)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="source-runtime", daemon=True)
    _thread.start()
    print("[sources] runtime started")


def stop() -> None:
    _stop.set()
