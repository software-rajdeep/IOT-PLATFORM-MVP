"""Built-in connector simulator.

Runs the REAL connector in simulation mode for every active device whose
connection has simulate=true, then pushes the raw output through the exact same
processing pipeline as a live gateway. Disable with ENABLE_SIMULATOR=0.
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
from typing import Optional

from .db import fetch_all
from .drivers import get_driver_class
from .services.devices import connector_config, load_device
from .services.instances import instances_for_device, load_config
from .services.processing import process_measurement

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _simulated_device_ids() -> list[str]:
    rows = fetch_all(
        "SELECT d.device_id, c.connection_parameters FROM core.devices d "
        "LEFT JOIN core.device_connections c ON c.device_id = d.device_id "
        "WHERE d.status = 'active'"
    )
    ids = []
    for row in rows:
        params = _as_dict(row.get("connection_parameters"))
        if params.get("simulate", True):
            ids.append(row["device_id"])
    return ids


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


def _loop(interval: float = 0.5) -> None:
    t0 = time.time()
    while not _stop.is_set():
        started = time.monotonic()
        for device_id in _simulated_device_ids():
            try:
                device = load_device(device_id)
                if not device:
                    continue
                connector_cls = get_driver_class(device["connector_type"])
                if not connector_cls:
                    continue
                config = connector_config(device)
                centre = _centre_displacement(device_id)
                if centre is None:
                    centre = 1.5
                # physical world: material at target thickness with slow drift + noise
                t = time.time() - t0
                config["simulate_displacement_mm"] = centre + 0.12 * math.sin(t / 6.0)
                connector = connector_cls(config, {"centre_distance": device.get("centre_distance")})
                reading = connector.readMeasurement()
                process_measurement(device_id, reading.get("measurement_raw"))
            except Exception as exc:
                print(f"[simulator] device {device_id} failed: {exc}")
        elapsed = time.monotonic() - started
        _stop.wait(max(0.05, interval - elapsed))


def start(interval: float = 0.5) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), name="connector-simulator", daemon=True)
    _thread.start()
    print("[simulator] started")


def stop() -> None:
    _stop.set()
