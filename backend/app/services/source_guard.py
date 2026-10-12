"""Guards for customer-supplied hosts/URLs (discovery, API pull, DB pull).

- Never let a tenant point us at the platform's own database.
- Block private / loopback addresses unless ALLOW_PRIVATE_SOURCES=1 (dev + LAN gateways).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from psycopg2.extensions import parse_dsn

from ..config import settings


class SourceBlocked(ValueError):
    pass


def _addresses(host: str) -> list:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise SourceBlocked(f"Cannot resolve host {host!r}")
    return [ipaddress.ip_address(i[4][0]) for i in infos]


def _is_private(addr) -> bool:
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


def check_host(host: str, port=None, dbname=None) -> None:
    if not host:
        raise SourceBlocked("No host configured")
    own = parse_dsn(settings.database_url)
    own_host = own.get("host") or "localhost"
    own_port = int(own.get("port") or 5432)
    if dbname and dbname == own.get("dbname") and int(port or 5432) == own_port:
        own_addrs = set(_addresses(own_host)) if own_host else set()
        if own_addrs & set(_addresses(host)):
            raise SourceBlocked("That is the platform's own database; it cannot be used as a customer source.")
    if not settings.allow_private_sources and any(_is_private(a) for a in _addresses(host)):
        raise SourceBlocked(f"Private/loopback address not allowed for {host!r} (set ALLOW_PRIVATE_SOURCES=1 for LAN sources)")


def check_url(url: str) -> None:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise SourceBlocked("Only http(s) URLs are allowed")
    check_host(parsed.hostname or "")
