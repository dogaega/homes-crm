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


def family_cim(client, mode: str, results: list, match=None) -> None:
    f = PoliteFetcher()
    for a in agencies(match):
        if not a.get("cim_listing_count"):
            continue
        slug = a["cim_slug"]
        info = {k: a[k] for k in ("name", "cim_slug", "manager", "address", "website", "phone", "email") if a.get(k)}
        results.append(run_site(f, client, f"cim-{slug}"[:60], info, lambda s=slug: cim.crawl_index(f, s),
                                cim.parse_detail, mode=mode))


def family_mcre(client, mode: str, results: list, match=None) -> None:
    f = PoliteFetcher()
    for a in agencies(match):
        if not a.get("mcre_listing_count"):
            continue
        slug = a["mcre_slug"]
        info = {k: a[k] for k in ("name", "cim_slug", "address", "website", "phone") if a.get(k)}
        results.append(run_site(f, client, f"mcre-{a['mcre_tc']}", info, lambda s=slug: mcre.crawl_index(f, s),
                                mcre.parse_detail, mode=mode))


def web_site(client, mode: str, cfg: dict, agency: dict) -> dict:
    from scraper.autoconfig import OUTSIDE
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
        return d

    info = {k: agency[k] for k in ("name", "cim_slug", "website", "phone", "email", "address") if agency.get(k)}
    return run_site(f, client, cfg["site_key"], info, index, detail, mode=mode, runner=cfg.get("runner", "server"))


def family_web(client, mode: str, results: list, match=None) -> None:
    """Every agency's own website with a usable config, 6 hosts at a time."""
    from concurrent.futures import ThreadPoolExecutor
    by_name = {a["name"]: a for a in agencies(None)}
    cfgs = [c for c in json.loads((DATA / "site_configs.json").read_text())
            if c.get("listing_pattern") and c.get("status") in ("ok", "weak", "verified")
            and (not match or any(m.lower() in c["agency"].lower() for m in match))]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results += list(ex.map(lambda c: web_site(client, mode, c, by_name.get(c["agency"], {"name": c["agency"]})), cfgs))


FAMILIES = {"cim": family_cim, "mcre": family_mcre, "web": family_web}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "light"], default="full")
    ap.add_argument("--only", choices=list(FAMILIES), action="append")
    ap.add_argument("--agency", action="append", help="substring of agency name (repeatable)")
    args = ap.parse_args()
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
