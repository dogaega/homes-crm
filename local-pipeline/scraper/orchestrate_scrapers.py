"""
Monaco/Riviera Local Pipeline — Camoufox Scraper Orchestrator
==================================================================
Main controller for the multi-threaded scraping run:

    1. Reads active sites from the local staging database (`sites` table).
    2. For each site, spins up an isolated Camoufox anti-detect browser
       instance with a randomized, realistic hardware/OS fingerprint.
    3. Dynamically loads that site's parser (parsers/<site_name>_parser.py)
       and runs it — every listing it finds is forwarded straight into
       `ClusterEngine.ingest()` (Task 3), so competing agencies at the same
       coordinates cluster under one parent instead of creating duplicates.
    4. Isolates failures per-site: a crashed browser, a blocked portal, or a
       parser bug on one site is logged and skipped — it never aborts the
       run for the other 29-39 sites.
    5. Logs a metrics summary (listings found/ingested, new parents/
       children, errors) per site and for the whole run.

Requires:
    pip install camoufox[geoip]
    python -m camoufox fetch      # downloads the patched Firefox build, once

Usage:
    python orchestrate_scrapers.py
    python orchestrate_scrapers.py --max-concurrent 4
    python orchestrate_scrapers.py --only miells,lorenza
    python orchestrate_scrapers.py --headful   # watch it run, for debugging one parser
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))       # for `parsers.*`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for `dedup.*`

from dedup.cluster_logic import ClusterEngine  # noqa: E402
from parsers.base_parser import BaseSiteParser, SiteRunMetrics  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("orchestrate_scrapers")

DEFAULT_MAX_CONCURRENT_BROWSERS = 6  # bounds RAM/CPU use across 30-40 sites
FINGERPRINT_OS_POOL = ["windows", "macos", "linux"]


@dataclass(frozen=True)
class Site:
    id: int
    name: str
    base_url: str
    is_active: bool


# ----------------------------------------------------------------------------
# Site registry (from the local staging DB)
# ----------------------------------------------------------------------------

def load_active_sites(db_path: Path, only: Optional[set[str]] = None) -> list[Site]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, base_url, is_active FROM sites WHERE is_active = 1 ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    sites = [Site(id=r["id"], name=r["name"], base_url=r["base_url"], is_active=bool(r["is_active"])) for r in rows]
    if only:
        sites = [s for s in sites if s.name in only]
    return sites


def load_parser_class(site_name: str) -> type[BaseSiteParser]:
    """
    Resolution order for a given site:

      1. A bespoke parsers/<site_name>_parser.py defining a class `Parser`
         (subclassing BaseSiteParser) — used for any site whose structure
         doesn't fit one of the 4 Archetypes, or where hand-tuned logic is
         worth the extra file. Always takes priority when present.
      2. A `site_configs.SITE_CONFIGS[site_name]` entry — the config-driven
         path used for the bulk of the 100-site target list, routed
         through the matching Archetype handler in
         parsers.dynamic_parser.DynamicSiteParser.

    This is what lets the orchestrator scale to 100 portals without 100
    hand-written parser files, while still allowing a bespoke override
    for any site that needs one.
    """
    module_name = f"parsers.{site_name}_parser"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        module = None

    if module is not None:
        parser_cls = getattr(module, "Parser", None)
        if parser_cls is None or not issubclass(parser_cls, BaseSiteParser):
            raise RuntimeError(
                f"{module_name} exists but must define a class `Parser` subclassing BaseSiteParser"
            )
        return parser_cls

    from site_configs import SITE_CONFIGS
    from parsers.dynamic_parser import make_dynamic_parser_class

    config = SITE_CONFIGS.get(site_name)
    if config is None:
        raise RuntimeError(
            f"No parser available for site '{site_name}': no parsers/{site_name}_parser.py "
            f"file and no site_configs.SITE_CONFIGS['{site_name}'] entry."
        )

    if config.requires_tos_review:
        logger.warning(
            "[%s] This site is flagged requires_tos_review=True (Archetype D / "
            "classified portal with active anti-bot protection). Confirm you have "
            "a lawful basis to scrape it (API/licensing agreement, or your own "
            "legal review of its Terms of Service) before running this in "
            "production. Proceeding with plain meta-tag extraction only — no "
            "CAPTCHA-solving or proxy-rotation logic is implemented.",
            site_name,
        )

    return make_dynamic_parser_class(config)


# ----------------------------------------------------------------------------
# Camoufox browser lifecycle
# ----------------------------------------------------------------------------

@contextmanager
def camoufox_page(headless: bool = True) -> Iterator[object]:
    """
    Launches one isolated Camoufox instance with a randomized, realistic
    hardware fingerprint (OS, screen, fonts, WebGL/canvas noise, navigator
    properties) and humanized interaction timing, so each of the 30-40
    concurrent sessions looks like a distinct real visitor rather than a
    fleet of identical bots. Yields a ready-to-use Playwright `page`.
    """
    from camoufox.sync_api import Camoufox

    chosen_os = random.choice(FINGERPRINT_OS_POOL)
    with Camoufox(
        headless=headless,
        os=chosen_os,
        humanize=True,          # human-like mouse/scroll jitter and timing
        geoip=True,              # matches timezone/locale to the exit IP's geo
        block_images=False,      # some portals lazy-load listing data via image-triggered JS
        i_know_what_im_doing=True,
    ) as browser:
        page = browser.new_page()
        try:
            yield page
        finally:
            page.close()


# ----------------------------------------------------------------------------
# Per-site worker
# ----------------------------------------------------------------------------

def run_site(site: Site, db_path: Path, headless: bool) -> SiteRunMetrics:
    """
    Runs exactly one site end-to-end, catching and reporting ANY failure
    (import error, browser crash, network failure, parser bug) as a metrics
    result rather than letting it propagate — the caller's job is only to
    decide concurrency, never to catch per-site exceptions itself.
    """
    started = time.monotonic()
    try:
        parser_cls = load_parser_class(site.name)
    except RuntimeError as exc:
        logger.error("[%s] %s", site.name, exc)
        metrics = SiteRunMetrics(site_name=site.name)
        metrics.record_error(str(exc))
        metrics.duration_seconds = time.monotonic() - started
        return metrics

    # Each site gets its OWN ClusterEngine connection (SQLite handles
    # concurrent writers via its own locking, and short-lived per-site
    # connections avoid holding a lock across a whole scrape).
    try:
        with ClusterEngine(db_path) as engine, camoufox_page(headless=headless) as page:
            parser = parser_cls(page, engine)
            metrics = parser.run()
    except Exception as exc:  # noqa: BLE001 - a browser-launch/crash must not kill the whole orchestrator
        logger.error("[%s] Site run crashed: %s", site.name, exc, exc_info=True)
        metrics = SiteRunMetrics(site_name=site.name)
        metrics.record_error(f"Site run crashed: {exc}")
        metrics.duration_seconds = time.monotonic() - started
        return metrics

    return metrics


# ----------------------------------------------------------------------------
# Main orchestration loop
# ----------------------------------------------------------------------------

def run_all(
    db_path: Path,
    max_concurrent: int,
    headless: bool,
    only: Optional[set[str]],
) -> list[SiteRunMetrics]:
    sites = load_active_sites(db_path, only=only)
    if not sites:
        logger.warning("No active sites found in %s (sites table empty or all inactive).", db_path)
        return []

    logger.info(
        "Starting scrape run: %d active site(s), max %d concurrent browser(s).",
        len(sites), max_concurrent,
    )

    all_metrics: list[SiteRunMetrics] = []
    with ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="site") as pool:
        futures = {pool.submit(run_site, site, db_path, headless): site for site in sites}
        for future in as_completed(futures):
            site = futures[future]
            try:
                metrics = future.result()
            except Exception as exc:  # noqa: BLE001 - absolute last-resort safety net
                logger.error("[%s] Unhandled exception escaped run_site: %s", site.name, exc, exc_info=True)
                metrics = SiteRunMetrics(site_name=site.name)
                metrics.record_error(f"Unhandled: {exc}")
            all_metrics.append(metrics)

    return all_metrics


def log_summary(all_metrics: list[SiteRunMetrics], total_duration: float) -> None:
    logger.info("=" * 78)
    logger.info("SCRAPE RUN SUMMARY")
    logger.info("=" * 78)
    header = f"{'Site':<20}{'Found':>8}{'Ingested':>10}{'NewParents':>12}{'NewChild':>10}{'Errors':>8}{'Time(s)':>10}"
    logger.info(header)
    logger.info("-" * len(header))

    totals = {"found": 0, "ingested": 0, "new_parents": 0, "new_children": 0, "errors": 0}
    for m in sorted(all_metrics, key=lambda m: m.site_name):
        logger.info(
            f"{m.site_name:<20}{m.listings_found:>8}{m.listings_ingested:>10}"
            f"{m.new_parents:>12}{m.new_children:>10}{m.errors:>8}{m.duration_seconds:>10.1f}"
        )
        totals["found"] += m.listings_found
        totals["ingested"] += m.listings_ingested
        totals["new_parents"] += m.new_parents
        totals["new_children"] += m.new_children
        totals["errors"] += m.errors
        for sample in m.error_samples:
            logger.warning("  [%s] %s", m.site_name, sample)

    logger.info("-" * len(header))
    logger.info(
        f"{'TOTAL':<20}{totals['found']:>8}{totals['ingested']:>10}"
        f"{totals['new_parents']:>12}{totals['new_children']:>10}{totals['errors']:>8}{total_duration:>10.1f}"
    )
    logger.info("=" * 78)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("STAGING_DB_PATH", "../dedup/pipeline_staging.db")),
        help="Path to the SQLite staging database.",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_BROWSERS,
        help="Maximum number of Camoufox browser instances running at once.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated list of site names to run (default: all active sites).",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run browsers with a visible window (default: headless). Useful while developing one parser.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        logger.error(
            "Staging database not found at %s. Run "
            "`sqlite3 %s < ../db/staging_schema.sql` first.",
            args.db, args.db,
        )
        return 1

    only = set(args.only.split(",")) if args.only else None

    started = time.monotonic()
    all_metrics = run_all(
        db_path=args.db,
        max_concurrent=args.max_concurrent,
        headless=not args.headful,
        only=only,
    )
    total_duration = time.monotonic() - started

    if not all_metrics:
        return 1

    log_summary(all_metrics, total_duration)
    # Per-site errors are logged and counted in the summary, but never fail
    # the run's exit code — a partial run (39/40 sites healthy) is a normal
    # outcome, not a pipeline failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
