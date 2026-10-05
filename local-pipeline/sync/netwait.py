"""
The Mac runner often sits on a phone hotspot that drops for minutes at a
time. A failed request while the internet is down is not the site's fault:
wait for the connection to come back instead of burning retries and marking
sites blocked. Stdlib only (used by worker_client too).
"""

from __future__ import annotations

import logging
import socket
import time

log = logging.getLogger("netwait")
PROBES = ("one.one.one.one", "google.com", "cloudflare.com")


def online() -> bool:
    for host in PROBES:
        try:
            socket.getaddrinfo(host, 443)
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
