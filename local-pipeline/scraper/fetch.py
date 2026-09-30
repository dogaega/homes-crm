"""
Polite HTTP fetcher shared by the plain-HTML scrapers (sites that need no
browser). PLAN.md rules: random 3–8 s between requests; on a block
(403/429/503, connection reset, challenge page) wait 30 s and retry ×3,
then raise Blocked so the run is reported 'blocked' — which never marks
listings removed.
"""

from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger("fetch")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]
BLOCK_STATUS = {403, 429, 503}
CHALLENGE_MARKERS = ("cf-challenge", "captcha", "Just a moment...", "Access denied")


class Blocked(RuntimeError):
    pass


class NotFound(RuntimeError):
    pass


class PoliteFetcher:
    def __init__(self, delay: tuple[float, float] = (3, 8), block_wait: float = 30, retries: int = 3,
                 timeout: float = 30):
        self.delay = delay
        self.block_wait = block_wait
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last = 0.0
        self.requests = 0

    def get(self, url: str) -> str:
        for attempt in range(self.retries + 1):
            wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    raise NotFound(url)
                if r.status_code in BLOCK_STATUS or any(m in r.text[:5000] for m in CHALLENGE_MARKERS):
                    reason = f"HTTP {r.status_code}"
                elif r.ok:
                    return r.text
                else:
                    reason = f"HTTP {r.status_code}"
            except (requests.ConnectionError, requests.Timeout) as e:
                reason = type(e).__name__
            if attempt < self.retries:
                log.warning("blocked/failed %s (%s), waiting %ss", url, reason, self.block_wait)
                time.sleep(self.block_wait)
                self.session.headers["User-Agent"] = random.choice(USER_AGENTS)
        raise Blocked(f"{url}: {reason}")
