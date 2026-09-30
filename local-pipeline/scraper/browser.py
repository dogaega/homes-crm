"""
Headless-Chromium fetcher for agency sites whose listing grids are built by
JavaScript. Same interface as PoliteFetcher.get(); one browser per instance
(Playwright's sync API is per-thread, so each site thread owns its own).

Chromium binary: PLAYWRIGHT_CHROMIUM (default: the pre-installed build under
/opt/pw-browsers); on the Mac, Playwright's own download is used.
"""

from __future__ import annotations

import glob
import logging
import os
import random
import time

from scraper.fetch import BLOCK_STATUS, CHALLENGE_MARKERS, Blocked, NotFound

log = logging.getLogger("browser")


def _chromium() -> str | None:
    if os.environ.get("PLAYWRIGHT_CHROMIUM"):
        return os.environ["PLAYWRIGHT_CHROMIUM"]
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return found[-1] if found else None


class BrowserFetcher:
    def __init__(self, delay: tuple[float, float] = (3, 8), block_wait: float = 30, retries: int = 2,
                 timeout: float = 45):
        from playwright.sync_api import sync_playwright
        self.delay, self.block_wait, self.retries, self.timeout = delay, block_wait, retries, timeout
        self._pw = sync_playwright().start()
        exe = _chromium()
        self._browser = self._pw.chromium.launch(executable_path=exe) if exe else self._pw.chromium.launch()
        self._last = 0.0
        self.requests = 0

    def _page(self):
        ctx = self._browser.new_context(
            locale="fr-FR", viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/128.0.0.0 Safari/537.36")
        return ctx, ctx.new_page()

    def get(self, url: str) -> str:
        reason = "unknown"
        for attempt in range(self.retries + 1):
            wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            ctx, page = self._page()
            try:
                resp = page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                # Lazy grids load on scroll.
                for _ in range(4):
                    page.mouse.wheel(0, 2500)
                    time.sleep(0.6)
                status = resp.status if resp else 0
                html = page.content()
                if status == 404:
                    raise NotFound(url)
                if status in BLOCK_STATUS or any(m in html[:5000] for m in CHALLENGE_MARKERS):
                    reason = f"HTTP {status}"
                else:
                    return html
            except NotFound:
                raise
            except Exception as e:
                reason = type(e).__name__
            finally:
                ctx.close()
            if attempt < self.retries:
                log.warning("browser blocked/failed %s (%s), waiting %ss", url, reason, self.block_wait)
                time.sleep(self.block_wait)
        raise Blocked(f"{url}: {reason}")

    def get_bytes(self, url: str) -> bytes:
        from scraper.fetch import PoliteFetcher
        return PoliteFetcher(delay=(0.5, 1.5)).get_bytes(url)

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()
