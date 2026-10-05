"""
The Mac runner often sits on a phone hotspot that drops for minutes at a
time. A failed request while the internet is down is not the site's fault:
wait for the connection to come back instead of burning retries and marking
sites blocked. Stdlib only (used by worker_client too).

The phone's own DNS forwarder also chokes under the thousands of lookups a
discovery run makes, and then every new site looks unreachable while the
internet is fine. Importing this module therefore sends name lookups to public
DNS-over-HTTPS (Cloudflare, then Google, by IP: no lookup needed to reach them),
with a cache, and falls back to the system resolver. Process-local: the Mac's
own network settings are untouched. MONACO_SYSTEM_DNS=1 turns it off.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import ssl
import threading
import time
from http.client import HTTPSConnection

log = logging.getLogger("netwait")
DOH = ("1.1.1.1", "8.8.8.8")
_system_getaddrinfo = socket.getaddrinfo
_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()
_ctx = ssl.create_default_context()


def _doh(host: str) -> str | None | bool:
    """IPv4 address, None when the name does not exist, False when no DoH server answered."""
    for server in DOH:
        try:
            c = HTTPSConnection(server, 443, timeout=5, context=_ctx)
            c.request("GET", f"/resolve?name={host}&type=A" if server == "8.8.8.8" else f"/dns-query?name={host}&type=A",
                      headers={"Accept": "application/dns-json"})
            data = json.loads(c.getresponse().read())
            c.close()
        except (OSError, ValueError):
            continue
        if data.get("Status") == 3:  # NXDOMAIN
            return None
        ips = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
        if ips:
            return ips[0]
        if data.get("Status") == 0:
            return None  # exists without an A record (or CNAME to nothing)
    return False


def _getaddrinfo(host, port, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if not name or name == "localhost" or name.endswith(".local"):
        return _system_getaddrinfo(host, port, *args, **kwargs)
    try:
        ipaddress.ip_address(name.strip("[]"))
        return _system_getaddrinfo(host, port, *args, **kwargs)
    except ValueError:
        pass
    key = name.lower().rstrip(".")
    with _lock:
        hit = _cache.get(key)
    if not hit or hit[0] < time.monotonic():
        ip = _doh(key)
        if ip is False:  # public DNS unreachable: let the system try
            return _system_getaddrinfo(host, port, *args, **kwargs)
        hit = (time.monotonic() + (1800 if ip else 600), ip)
        with _lock:
            _cache[key] = hit
    if hit[1] is None:
        raise socket.gaierror(socket.EAI_NONAME, f"{name}: name does not exist")
    return _system_getaddrinfo(hit[1], port, *args, **kwargs)


if os.environ.get("MONACO_SYSTEM_DNS") != "1":
    socket.getaddrinfo = _getaddrinfo


def online() -> bool:
    # A TCP connection to a public resolver by IP: no DNS involved, so a
    # cached or broken local resolver cannot make the answer wrong.
    for server in DOH:
        try:
            socket.create_connection((server, 443), timeout=5).close()
            return True
        except OSError:
            continue
    return False


def wait_for_network(max_wait: float = 3 * 3600) -> bool:
    """True at once when online; otherwise poll until the connection is back
    (or max_wait passes, then False)."""
    if online():
        return True
    log.warning("internet is down — waiting for the connection to come back")
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        time.sleep(20)
        if online():
            log.warning("internet is back after %d s", time.monotonic() - start)
            return True
    return False
