"""
Generic site run shared by every scraper (PLAN.md "Daily flow per site"):

    crawl the whole index (cheap) → report sightings → open detail pages only
    for listings the Worker doesn't know, plus a rotating refresh of details
    older than DETAIL_REFRESH_DAYS → finish with honest status.

A scraper module provides crawl_index() -> [{source_url, price?, title?}] and
parse_detail(html, url) -> listing dict. Never raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from scraper.fetch import Blocked, NotFound, PoliteFetcher
from scraper.hero import upload_hero

log = logging.getLogger("runner")

DETAIL_REFRESH_DAYS = 7
# Set by `daily.py --refresh-details`: re-fetch every known listing's detail
# page (after an extractor fix), not just the weekly rotation.
REFRESH_ALL = False


def run_site(f: PoliteFetcher, client, site_key: str, agency: dict,
             crawl_index: Callable[[], list[dict]],
             parse_detail: Callable[[str, str], dict | None],
             *, runner: str = "server", mode: str = "full", limit: int | None = None,
             heroes: bool = True) -> dict:
    from sync.worker_client import SiteRun
    try:
        with SiteRun(client, site_key, runner=runner, mode=mode, agency=agency) as run:
            try:
                known = run.known()
                cards = crawl_index()
                unknown = set(run.seen(cards))
                if mode == "light":
                    todo = [c["source_url"] for c in cards if c["source_url"] in unknown]
                else:
                    stale = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat() if REFRESH_ALL else \
                        (datetime.now(timezone.utc) - timedelta(days=DETAIL_REFRESH_DAYS)).isoformat()
                    todo = [c["source_url"] for c in cards if c["source_url"] in unknown] + [
                        c["source_url"] for c in cards
                        if c["source_url"] in known and (known[c["source_url"]].get("detail_scraped_at") or "") < stale]
                complete = not limit or len(todo) <= limit
                todo = todo[:limit] if limit else todo
                details = 0
                for url in todo:
                    try:
                        listing = parse_detail(f.get(url), url)
                        if listing is None:  # out of scope (e.g. outside Monaco)
                            continue
                        res = run.push([listing])[0]
                        details += 1
                        if heroes and res.get("source_id") and not known.get(url, {}).get("has_hero"):
                            upload_hero(f, run, res["source_id"], listing.get("photo_urls") or [])
                    except NotFound:
                        log.info("%s: %s gone before detail fetch", site_key, url)
                    except Blocked:
                        raise
                    except Exception:
                        log.exception("%s: detail failed for %s", site_key, url)
                # Details still owed are picked up next run (needs_detail);
                # only a run that finished them all counts for removals.
                return run.finish("ok" if complete else "partial", index_complete=complete,
                                  index_count=len(cards), detail_count=details)
            except Blocked as e:
                return run.finish("blocked", error=str(e))
    except Exception as e:  # Worker unreachable etc. — never crash the caller
        log.exception("%s: run failed", site_key)
        return {"site_key": site_key, "status": "failed", "error": repr(e)}
