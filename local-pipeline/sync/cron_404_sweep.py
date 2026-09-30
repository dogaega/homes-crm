"""
Monaco/Riviera Local Pipeline — Daily 404 Sweep
===================================================
Runs every morning against the live CRM (Cloudflare Workers API):

    1. GET  /sync/properties/urls       -> every listing URL still marked live
    2. HEAD/GET each URL with a short timeout
    3. POST /sync/sources/:id/checkin   -> report found=True/False

The Worker owns the "3 consecutive misses -> off-market" rule (see
worker/src/index.ts handleSync, migration 0005_pipeline_sync.sql); this
script's only job is an honest, conservative liveness check per URL.

Conservative by design: only an explicit HTTP 404 counts as "not found".
Timeouts, connection errors, 403s (anti-bot blocks), 5xx, etc. are
ambiguous — they do NOT report found=False, because that would risk
archiving a property that's actually still live but temporarily
unreachable. Those URLs are simply skipped for the day and picked up again
on the next run.

Requires:
    pip install requests

Environment variables:
    SYNC_API_BASE_URL   Base URL of the Workers API, e.g.
                         https://monaco-riviera-crm.<subdomain>.workers.dev
                         (or the proxied https://yourapp.com/api/backend)
    SYNC_API_TOKEN       Same shared secret configured as the Worker's
                          SYNC_API_TOKEN (see wrangler secret put)

Usage:
    python cron_404_sweep.py
    python cron_404_sweep.py --workers 20 --timeout 15
    python cron_404_sweep.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cron_404_sweep")

DEFAULT_TIMEOUT_SECONDS = 12
DEFAULT_WORKERS = 10
NETWORK_RETRY_ATTEMPTS = 2

# A plain, honest desktop user agent. This is a liveness check of OUR OWN
# database's URLs, not scraping — no fingerprint evasion needed or wanted
# here.
USER_AGENT = "MonacoRivieraCRM-LinkChecker/1.0 (+internal pipeline health check)"


@dataclass(frozen=True)
class SourceUrl:
    id: str
    source_url: str
    consecutive_404_count: int


@dataclass(frozen=True)
class CheckResult:
    source: SourceUrl
    outcome: str  # 'found' | 'not_found' | 'ambiguous'
    detail: str


class SyncApiClient:
    def __init__(self, base_url: str, token: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        })
        retry = Retry(
            total=NETWORK_RETRY_ATTEMPTS,
            backoff_factor=1.0,
            status_forcelist=[502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_active_urls(self) -> list[SourceUrl]:
        resp = self.session.get(f"{self.base_url}/sync/properties/urls", timeout=self.timeout)
        resp.raise_for_status()
        rows = resp.json()
        return [
            SourceUrl(
                id=row["id"],
                source_url=row["source_url"],
                consecutive_404_count=row.get("consecutive_404_count", 0),
            )
            for row in rows
            if row.get("source_url")
        ]

    def checkin(self, source_id: str, found: bool) -> dict:
        resp = self.session.post(
            f"{self.base_url}/sync/sources/{source_id}/checkin",
            json={"found": found},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------------
# Liveness check
# ----------------------------------------------------------------------------

def check_url(source: SourceUrl, timeout: int) -> CheckResult:
    headers = {"User-Agent": USER_AGENT}
    try:
        # HEAD first (cheap); some portals don't support it well, so fall
        # back to a real GET before drawing any conclusion.
        resp = requests.head(
            source.source_url, headers=headers, timeout=timeout, allow_redirects=True
        )
        if resp.status_code == 405 or resp.status_code >= 500:
            resp = requests.get(
                source.source_url, headers=headers, timeout=timeout, allow_redirects=True,
                stream=True,
            )
    except requests.exceptions.RequestException as exc:
        return CheckResult(source, "ambiguous", f"Network error: {exc}")

    if resp.status_code == 404:
        return CheckResult(source, "not_found", "HTTP 404")

    if 200 <= resp.status_code < 400:
        return CheckResult(source, "found", f"HTTP {resp.status_code}")

    # 403 (bot-block), 429 (rate limited), other 5xx after fallback, etc.
    # Never conclude "gone" from these — just inconclusive for today.
    return CheckResult(source, "ambiguous", f"HTTP {resp.status_code} (inconclusive)")


# ----------------------------------------------------------------------------
# Main sweep
# ----------------------------------------------------------------------------

def run_sweep(base_url: str, token: str, workers: int, timeout: int, dry_run: bool) -> int:
    client = SyncApiClient(base_url, token, timeout)

    try:
        sources = client.fetch_active_urls()
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to fetch active URLs from %s: %s", base_url, exc)
        return 1

    if not sources:
        logger.info("No active listing URLs to check.")
        return 0

    logger.info("Checking %d listing URL(s) with %d worker thread(s)...", len(sources), workers)

    results: list[CheckResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_url, s, timeout): s for s in sources}
        for future in as_completed(futures):
            results.append(future.result())

    found_count = sum(1 for r in results if r.outcome == "found")
    not_found_count = sum(1 for r in results if r.outcome == "not_found")
    ambiguous_count = sum(1 for r in results if r.outcome == "ambiguous")

    logger.info(
        "Check complete: %d found, %d confirmed 404, %d ambiguous (skipped).",
        found_count, not_found_count, ambiguous_count,
    )

    for result in results:
        if result.outcome == "ambiguous":
            logger.warning(
                "SKIP checkin for %s (%s): %s",
                result.source.id, result.source.source_url, result.detail,
            )
            continue

        found = result.outcome == "found"

        if dry_run:
            projected = 0 if found else result.source.consecutive_404_count + 1
            logger.info(
                "[DRY RUN] Would report found=%s for %s (%s) — "
                "consecutive_404_count %d -> %d%s",
                found, result.source.id, result.source.source_url,
                result.source.consecutive_404_count, projected,
                " (WOULD ARCHIVE)" if projected >= 3 else "",
            )
            continue

        try:
            outcome = client.checkin(result.source.id, found)
        except requests.exceptions.RequestException as exc:
            logger.error("Checkin failed for %s: %s", result.source.id, exc)
            continue

        if outcome.get("is_off_market"):
            logger.warning(
                "Source %s (%s) hit 3 consecutive 404s — now off-market.",
                result.source.id, result.source.source_url,
            )
        elif not found:
            logger.info(
                "Source %s (%s) returned 404 (%d/3 consecutive).",
                result.source.id, result.source.source_url,
                outcome.get("consecutive_404_count", "?"),
            )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.getenv("SYNC_API_BASE_URL"),
        help="Base URL of the Workers API (default: $SYNC_API_BASE_URL).",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("SYNC_API_TOKEN"),
        help="Bearer token for the /sync/* endpoints (default: $SYNC_API_TOKEN).",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check URLs and log outcomes without calling the checkin endpoint.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.api_url:
        logger.error("Missing --api-url / $SYNC_API_BASE_URL")
        return 1
    if not args.token:
        logger.error("Missing --token / $SYNC_API_TOKEN")
        return 1

    started = time.monotonic()
    exit_code = run_sweep(args.api_url, args.token, args.workers, args.timeout, args.dry_run)
    logger.info("Sweep finished in %.1fs", time.monotonic() - started)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
