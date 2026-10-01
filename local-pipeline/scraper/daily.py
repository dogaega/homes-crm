"""
Daily / light run over every backbone site.

    python -m scraper.daily              # full: new + stale details, removals
    python -m scraper.daily --mode light # index only + details for new listings
    python -m scraper.daily --only cim   # one family

Sites run in sequence per family (one polite fetcher per host), the two
families in parallel threads. Ends with the daily changelog (full mode).
Env: SYNC_API_BASE_URL, SYNC_API_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper import cim, generic, mcre  # noqa: E402
from scraper.fetch import PoliteFetcher  # noqa: E402
from scraper.runner import run_site  # noqa: E402
from sync.worker_client import WorkerClient  # noqa: E402

log = logging.getLogger("daily")
DATA = Path(__file__).resolve().parents[1] / "data"


def agencies(match: list[str] | None) -> list[dict]:
    rows = json.loads((DATA / "agencies.json").read_text())
    return [a for a in rows if not match or any(m.lower() in a["name"].lower() for m in match)]


# The two portals are large, unprotected sites: 2–5 s between requests is
# still gentle and keeps the full morning run within a few hours.
PORTAL_DELAY = (2, 5)


def family_cim(client, mode: str, results: list, match=None) -> None:
    f = PoliteFetcher(delay=PORTAL_DELAY)
    for a in agencies(match):
        if not a.get("cim_listing_count"):
            continue
        slug = a["cim_slug"]
        info = {k: a[k] for k in ("name", "cim_slug", "manager", "address", "website", "phone", "email") if a.get(k)}
        results.append(run_site(f, client, re.sub(r"[^a-z0-9_-]+", "-", f"cim-{slug}")[:60], info, lambda s=slug: cim.crawl_index(f, s),
                                cim.parse_detail, mode=mode))


def family_mcre(client, mode: str, results: list, match=None) -> None:
    f = PoliteFetcher(delay=PORTAL_DELAY)
    for a in agencies(match):
        if not a.get("mcre_listing_count"):
            continue
        slug = a["mcre_slug"]
        info = {k: a[k] for k in ("name", "cim_slug", "address", "website", "phone") if a.get(k)}
        results.append(run_site(f, client, f"mcre-{a['mcre_tc']}", info, lambda s=slug: mcre.crawl_index(f, s),
                                mcre.parse_detail, mode=mode))


def web_site(client, mode: str, cfg: dict, agency: dict) -> dict:
    from scraper.autoconfig import OUTSIDE
    if cfg.get("render"):
        from scraper.browser import BrowserFetcher
        f = BrowserFetcher()
    else:
        f = PoliteFetcher()
    hints: dict[str, str] = {}

    def index() -> list[dict]:
        cards = [c for c in generic.crawl_index(f, cfg) if not OUTSIDE.search(c["source_url"])]
        hints.update({c["source_url"]: c["transaction_hint"] for c in cards})
        return cards

    def detail(html: str, url: str) -> dict | None:
        d = generic.parse_detail(html, url, cfg, agency, hint=hints.get(url))
        if OUTSIDE.search(" ".join(str(d.get(k) or "") for k in ("title", "quarter"))) or not d.get("transaction_type"):
            return None
        # A listing has a price (or "on request") and some size: articles,
        # category and agency pages never pass this.
        has_price = d.get("price") is not None or bool(re.search(
            r"prix sur demande|price on request|sur demande|on application", html[:200000], re.I))
        has_size = any(d.get(k) is not None for k in ("living_area_sqm", "rooms", "bedrooms"))
        if not (has_price and has_size):
            log.info("%s: not a listing page, skipped: %s", cfg["site_key"], url)
            return None
        return d

    info = {k: agency[k] for k in ("name", "cim_slug", "website", "phone", "email", "address") if agency.get(k)}
    try:
        return run_site(f, client, cfg["site_key"], info, index, detail, mode=mode, runner=cfg.get("runner", "server"))
    finally:
        if hasattr(f, "close"):
            f.close()


RUNNER = "server"


def load_site_configs() -> list[dict]:
    """Auto-drafted configs, overridden per site_key by hand-written ones."""
    auto = {c["site_key"]: c for c in json.loads((DATA / "site_configs.json").read_text())}
    manual_path = DATA / "site_configs_manual.json"
    if manual_path.exists():
        for c in json.loads(manual_path.read_text()):
            auto[c["site_key"]] = {**auto.get(c["site_key"], {}), **c}
    return list(auto.values())


def family_web(client, mode: str, results: list, match=None) -> None:
    """Every agency's own website with a usable config for this runner
    (server: VPS/cloud; local: Mac, for sites that refuse server IPs)."""
    from concurrent.futures import ThreadPoolExecutor
    by_name = {a["name"]: a for a in agencies(None)}
    cfgs = [c for c in load_site_configs()
            if c.get("listing_pattern") and c.get("status") in ("ok", "weak", "verified")
            and c.get("runner", "server") == RUNNER
            and (not match or any(m.lower() in c["agency"].lower() for m in match))]
    # Many agency sites share one hosting server (e.g. ~40 Immotoolbox sites on
    # one IP): sites on the same server run one after another, servers in parallel.
    import socket
    from urllib.parse import urlparse
    groups: dict[str, list[dict]] = {}
    for c in cfgs:
        host = urlparse(c["website"]).netloc
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            ip = host
        groups.setdefault(ip, []).append(c)

    def run_group(group: list[dict]) -> list[dict]:
        return [web_site(client, mode, c, by_name.get(c["agency"], {"name": c["agency"]})) for c in group]

    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(run_group, sorted(groups.values(), key=len, reverse=True)):
            results += res


FAMILIES = {"cim": family_cim, "mcre": family_mcre, "web": family_web}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "light"], default="full")
    ap.add_argument("--only", choices=list(FAMILIES), action="append")
    ap.add_argument("--agency", action="append", help="substring of agency name (repeatable)")
    ap.add_argument("--runner", choices=["server", "local"], default="server",
                    help="local = Mac: only sites that block server IPs (implies --only web)")
    ap.add_argument("--refresh-details", action="store_true", help="re-fetch all detail pages (after parser fixes)")
    ap.add_argument("--refresh-before", help="re-fetch detail pages last scraped before this ISO time")
    args = ap.parse_args()
    if args.refresh_details:
        from scraper import runner
        runner.REFRESH_ALL = True
    if args.refresh_before:
        from scraper import runner
        runner.REFRESH_BEFORE = args.refresh_before
    global RUNNER
    RUNNER = args.runner
    if RUNNER == "local":
        args.only = ["web"]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = WorkerClient()
    results: list[dict] = []
    threads = [threading.Thread(target=fn, args=(client, args.mode, results, args.agency), name=name)
               for name, fn in FAMILIES.items() if not args.only or name in args.only]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.info("sites: %s", dict(Counter(r.get("status") for r in results)))
    if args.mode == "full":
        log.info("changelog: %s", client.changelog()["summary"])


if __name__ == "__main__":
    main()
