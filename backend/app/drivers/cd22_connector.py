"""BLOCK 1 — CD22RS485Connector

The ONLY code in the platform that understands the OPTEX CD22 RS-485 protocol.
Everything above it sees plain raw numbers; everything below it is bytes.

Request / response frame (Optex binary command set):
    request : [STX, CMD, ADDR_H, ADDR_L, ETX, BCC]   BCC = CMD ^ ADDR_H ^ ADDR_L
    response: [STX, ACK(0x06), DATA_H, DATA_L, ETX, BCC]   (NAK 0x15 on error)

Transports: a local serial port ("COM3", "/dev/ttyUSB0") or a serial-to-Ethernet
converter given as "host:port" (our field installs use TCP 8234). The CD22 is
single-client: one reader per sensor at a time (see _PORT_LOCKS).
The measurement register (CMD 0x43) returns a signed 16-bit count. It is NOT
scaled here — scaling is BLOCK 3 (normalization) via the device model.

Output of readMeasurement() is deliberately customer-agnostic:

    {
      "device_id": "DEV001",
      "timestamp": "2026-10-08T10:30:22.432+05:30",
      "measurement_raw": 151,
      "communication_status": "online"
    }
"""
from __future__ import annotations

import math
import random
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .base import DeviceDriver, DriverError
from .registry import register

STX = 0x02
ETX = 0x03
CMD_MEASURE = 0x43
CMD_OUTPUT_STATUS = 0x44
CMD_ADDR_H = 0xB0
CMD_ADDR_L = 0x01
BAUD_DEFAULT = 38400
ACK = 0x06
FRAME_LEN = 6

# One lock per physical port/endpoint: the CD22 interleaves bytes if two readers talk to it.
_PORT_LOCKS: dict[str, threading.Lock] = {}
_PORT_LOCKS_GUARD = threading.Lock()


def _port_lock(key: str) -> threading.Lock:
    with _PORT_LOCKS_GUARD:
        return _PORT_LOCKS.setdefault(key, threading.Lock())


def _bcc(*values: int) -> int:
    result = 0
    for value in values:
        result ^= value
    return result


@register
class CD22RS485Connector(DeviceDriver):
    connector_type = "cd22_rs485"
    driver_key = "cd22_rs485"  # registry key (connector_type)

    def __init__(self, connection: Optional[dict] = None, model: Optional[dict] = None):
        super().__init__(connection, model)
        self.port = self.config.get("port") or self.config.get("serial_port")
        self.baud_rate = int(self.config.get("baud_rate") or self.config.get("baudrate") or BAUD_DEFAULT)
        self.address = int(self.config.get("address") or self.config.get("device_address") or 1)
        self.timeout = float(self.config.get("timeout", 0.5))
        self.simulate = bool(self.config.get("simulate", self.port is None))
        self._serial = None
        self._t0 = time.time()

    # -- framing (protocol knowledge lives HERE and nowhere else) ----------
    @staticmethod
    def _build_frame(command: int, addr_h: int = CMD_ADDR_H, addr_l: int = CMD_ADDR_L) -> bytes:
        return bytes([STX, command, addr_h, addr_l, ETX, _bcc(command, addr_h, addr_l)])

    @staticmethod
    def _parse_value(response: bytes) -> Optional[int]:
        if not response or len(response) != FRAME_LEN:
            return None
        if response[0] != STX or response[1] != ACK or response[4] != ETX:
            return None
        if _bcc(response[1], response[2], response[3]) != response[5]:
            return None
        raw = (response[2] << 8) | response[3]
        if raw > 32767:
            raw -= 65536
        return raw

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        if self.simulate:
            return
        serial = None
        if not self._is_tcp:
            try:
                import serial  # pyserial, optional
            except ImportError as exc:  # pragma: no cover
                raise DriverError(
                    "pyserial is required for a real RS-485 connection; "
                    "set connection simulate=true to run without hardware."
                ) from exc
        if not self.port:
            raise DriverError("No serial port configured for the CD22 connector.")
        if self._is_tcp:
            host, port = str(self.port).rsplit(":", 1)
            self._serial = socket.create_connection((host, int(port)), timeout=self.timeout)
            self._serial.settimeout(self.timeout)
            return
        self._serial = serial.Serial(
            port=self.port, baudrate=self.baud_rate, bytesize=8,
            parity="N", stopbits=1, timeout=self.timeout,
        )

    def disconnect(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    @property
    def _is_tcp(self) -> bool:
        port = str(self.port or "")
        return ":" in port and port.rsplit(":", 1)[1].isdigit() and not port.upper().startswith("COM")

    def _read_exact(self, n: int) -> bytes:
        deadline = time.monotonic() + self.timeout
        buf = b""
        while len(buf) < n and time.monotonic() < deadline:
            if self._is_tcp:
                try:
                    chunk = self._serial.recv(n - len(buf))
                except socket.timeout:
                    break
                if not chunk:
                    break
            else:
                chunk = self._serial.read(n - len(buf))
                if not chunk:
                    break
            buf += chunk
        return buf

    # alias used by the base context manager
    open = connect
    close = disconnect

    # -- protocol operations ----------------------------------------------
    def _transact(self, command: int) -> Optional[int]:
        if self.simulate:
            return self._simulate_raw()
        with _port_lock(str(self.port)):
            try:
                if self._serial is None:
                    self.connect()
                if self._is_tcp:
                    self._serial.sendall(self._build_frame(command))
                else:
                    self._serial.reset_input_buffer()
                    self._serial.write(self._build_frame(command))
                return self._parse_value(self._read_exact(FRAME_LEN))
            except (OSError, socket.timeout):
                self.disconnect()
                return None

    def readMeasurement(self) -> dict:  # noqa: N802 (protocol name)
        raw = self._transact(CMD_MEASURE)
        return {
            "device_id": self.config.get("device_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "measurement_raw": raw,
            "communication_status": "online" if raw is not None else "offline",
        }

    def readOutputStatus(self) -> dict:  # noqa: N802
        value = self._transact(CMD_OUTPUT_STATUS)
        return {"output_status": value, "communication_status": "online" if value is not None else "offline"}

    def checkConnection(self) -> bool:  # noqa: N802
        if self.simulate:
            return True
        try:
            return self._transact(CMD_MEASURE) is not None
        except Exception:
            return False

    def readDeviceSettings(self) -> dict:  # noqa: N802
        # Register map for settings is filled in when the CD22 RS-485 manual is
        # consulted; exposed here so the connector stays the single protocol owner.
        return {"simulate": self.simulate, "baud_rate": self.baud_rate, "address": self.address}

    # backward-compatible simple read used by generic callers
    def read(self) -> dict:
        return self.readMeasurement()

    # -- simulation --------------------------------------------------------
    def _simulate_raw(self) -> int:
        """Plausible raw count for ~1.5 mm displacement with slow drift + noise.

        The connector simulates a *displacement*; calibration converts it to
        thickness downstream (BLOCK 4). Raw is count = displacement / 0.01.
        """
        centre = float(self.config.get("simulate_displacement_mm", 1.5))
        t = time.time() - self._t0
        displacement = centre + 0.15 * math.sin(t / 6.0) + random.uniform(-0.02, 0.02)
        return int(round(displacement / 0.01))
