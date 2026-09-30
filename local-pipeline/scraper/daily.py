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
from scraper import cim, mcre  # noqa: E402
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


FAMILIES = {"cim": family_cim, "mcre": family_mcre}


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
