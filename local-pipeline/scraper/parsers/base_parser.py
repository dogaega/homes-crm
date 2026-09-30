"""
Monaco/Riviera Local Pipeline — Base Site Parser
====================================================
Every portal-specific parser (parsers/<site_name>_parser.py) subclasses
`BaseSiteParser` and implements two required methods:

    get_listing_urls()              -> list[str]
    parse_listing(payload, url)     -> ScrapedListing

and optionally overrides a third:

    fetch_listing_payload(url)      -> Any   (default: pulls __NEXT_DATA__)

Everything else — navigating to each URL, handing the resulting listing to
the de-duplication engine, per-listing error isolation, and human-like
pacing between requests — is handled once here so individual parser files
stay small and only contain the site-specific extraction logic.

For the 100-portal target list, most sites don't need a bespoke file at
all: `parsers/dynamic_parser.py` implements `DynamicSiteParser`, a single
class that reads a `site_configs.SiteConfig` entry and routes through one
of four Archetype handlers (regional JSON API, JS-framework hydration,
boutique static/JS-map, classified meta-tags). Write a bespoke
`parsers/<site>_parser.py` only for a site whose structure doesn't fit any
of the four — it always takes priority over the config-driven fallback
(see orchestrate_scrapers.load_parser_class).
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# local-pipeline/dedup/cluster_logic.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dedup.cluster_logic import ClusterEngine, ClusterEngineError, ScrapedListing  # noqa: E402

logger = logging.getLogger("base_parser")

# Human-like pacing between listing page visits, in seconds.
MIN_REQUEST_DELAY = 1.8
MAX_REQUEST_DELAY = 4.5


class ParserError(RuntimeError):
    """Raised for a site-specific extraction failure (missing/unexpected data)."""


@dataclass
class SiteRunMetrics:
    site_name: str
    listings_found: int = 0
    listings_ingested: int = 0
    new_parents: int = 0
    new_children: int = 0
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def record_error(self, message: str) -> None:
        self.errors += 1
        if len(self.error_samples) < 5:
            self.error_samples.append(message)


class BaseSiteParser(ABC):
    """
    Subclass this per portal. `site_name` MUST match the `sites.name` row
    in the staging database (see local-pipeline/db/staging_schema.sql) —
    it is what child listings get attributed to.
    """

    site_name: str = ""

    def __init__(self, page: Any, engine: ClusterEngine):
        if not self.site_name:
            raise NotImplementedError(f"{type(self).__name__} must set a class-level site_name")
        self.page = page
        self.engine = engine

    # -- must be implemented per site ---------------------------------------

    @abstractmethod
    def get_listing_urls(self) -> list[str]:
        """
        Return every listing detail-page URL to visit this run (typically:
        navigate the search/listing index page(s) with `self.page` and
        collect detail-page hrefs, following pagination).
        """
        raise NotImplementedError

    @abstractmethod
    def parse_listing(self, payload: Any, url: str) -> ScrapedListing:
        """
        Given whatever `fetch_listing_payload()` returned for this URL
        (by default: the parsed `__NEXT_DATA__` JSON), build and return a
        ScrapedListing. Raise ParserError if a required field (coordinates,
        living area, price) is missing — never guess or silently default
        those.
        """
        raise NotImplementedError

    # -- override only if this site doesn't fit the __NEXT_DATA__ default ---

    def fetch_listing_payload(self, url: str) -> Any:
        """
        Navigates to `url` and returns whatever raw payload
        `parse_listing()` needs to build a ScrapedListing. Default
        behavior (most Monaco portals): pull the hidden Next.js
        `__NEXT_DATA__` JSON blob. Override this for a site that exposes
        its data differently (a JSON API call, raw HTML, meta tags, a
        different hydration variable, etc.) — see
        parsers.dynamic_parser.DynamicSiteParser for the config-driven
        per-archetype implementations.
        """
        self.page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        return self.extract_next_data()

    # -- shared machinery, do not override -----------------------------------

    def extract_next_data(self, var_name: str = "__NEXT_DATA__") -> Any:
        """
        Pulls a hidden framework hydration blob straight out of the page,
        instead of scraping the (fragile, frequently-restyled) visible
        markup. Defaults to Next.js's `<script id="__NEXT_DATA__">` tag;
        pass e.g. var_name="__NUXT__" for a Nuxt.js site that instead
        exposes its state as a `window.__NUXT__` global.
        """
        if var_name == "__NEXT_DATA__":
            raw = self.page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__'); "
                "return el ? el.textContent : null; }"
            )
        else:
            raw = self.page.evaluate(
                f"() => {{ const v = window['{var_name}']; return v ? JSON.stringify(v) : null; }}"
            )
        if not raw:
            raise ParserError(
                f"No {var_name} hydration data found on {self.page.url} — "
                f"page may have failed to render, or the portal changed its structure."
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ParserError(f"{var_name} on {self.page.url} was not valid JSON: {exc}") from exc

    @staticmethod
    def dig(data: dict, *path: str, required: bool = True) -> Any:
        """
        Small helper for walking a nested __NEXT_DATA__ path, e.g.
            self.dig(next_data, "props", "pageProps", "listing", "price")
        Raises ParserError with the full path on a miss, instead of a bare
        KeyError three stack frames away from anything useful.
        """
        node = data
        for i, key in enumerate(path):
            if not isinstance(node, dict) or key not in node:
                if required:
                    raise ParserError(f"Missing expected field at __NEXT_DATA__.{'.'.join(path[:i+1])}")
                return None
            node = node[key]
        return node

    def _human_delay(self) -> None:
        time.sleep(random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY))

    def run(self) -> SiteRunMetrics:
        metrics = SiteRunMetrics(site_name=self.site_name)
        started = time.monotonic()

        try:
            urls = self.get_listing_urls()
        except Exception as exc:  # noqa: BLE001 - isolate the whole site, not just one listing
            metrics.record_error(f"get_listing_urls failed: {exc}")
            metrics.duration_seconds = time.monotonic() - started
            logger.error("[%s] Could not enumerate listing URLs: %s", self.site_name, exc)
            return metrics

        metrics.listings_found = len(urls)
        logger.info("[%s] Found %d listing URL(s) to process.", self.site_name, len(urls))

        for i, url in enumerate(urls, start=1):
            try:
                payload = self.fetch_listing_payload(url)
                listing = self.parse_listing(payload, url)
                result = self.engine.ingest(listing)

                metrics.listings_ingested += 1
                if result.was_new_parent:
                    metrics.new_parents += 1
                if result.was_new_child:
                    metrics.new_children += 1

                logger.info(
                    "[%s] (%d/%d) %s -> parent #%d %s, child #%d %s",
                    self.site_name, i, len(urls), url,
                    result.parent_id, "(new)" if result.was_new_parent else "(matched)",
                    result.child_id, "(new)" if result.was_new_child else "(updated)",
                )
            except (ParserError, ClusterEngineError) as exc:
                metrics.record_error(f"{url}: {exc}")
                logger.warning("[%s] Skipping %s: %s", self.site_name, url, exc)
            except Exception as exc:  # noqa: BLE001 - one bad listing must never abort the site run
                metrics.record_error(f"{url}: unexpected {type(exc).__name__}: {exc}")
                logger.error("[%s] Unexpected error on %s: %s", self.site_name, url, exc, exc_info=True)
            finally:
                self._human_delay()

        metrics.duration_seconds = time.monotonic() - started
        return metrics
