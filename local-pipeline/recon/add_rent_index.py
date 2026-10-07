"""
Sites whose config only crawls sale pages: find the site's long-term rental
page (homepage links to /location, /louer, /rent …; holiday lets left out) and
add it as a rent index page when it links to listings matching the site's own
listing_pattern. Only auto configs (data/site_configs.json) are changed.

    python recon/add_rent_index.py [--workers 6]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper import generic  # noqa: E402
from scraper.fetch import PoliteFetcher  # noqa: E402
from sync.netwait import wait_for_network  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
RENT = re.compile(r"locat(?:ion|ions)(?!-?saison)|louer|/rent(?:al|als)?\b|for-rent|to-let|affitt", re.I)
HOLIDAY = re.compile(r"saison|vacance|holiday|seasonal|courte|short|gestion|syndic|estimation|vendre|proprietaire", re.I)


def find_rent_pages(cfg: dict) -> list[str]:
    f = PoliteFetcher(delay=(0.5, 1.5), retries=1, block_wait=5)
    pattern = re.compile(cfg["listing_pattern"])
    host = urlparse(cfg["website"]).netloc.lower().removeprefix("www.")
    try:
        home = f.get(cfg["website"])
    except Exception:
        return []
    cands = []
    for href in generic.card_links(generic.soup(home)):
        u = urljoin(cfg["website"], href).split("#")[0]
        p = urlparse(u)
        if p.netloc.lower().removeprefix("www.") != host or pattern.search(u):
            continue
        if RENT.search(p.path) and not HOLIDAY.search(p.path + "?" + p.query) and u not in cands:
            cands.append(u)
    out = []
    for u in cands[:6]:
        try:
            page = f.get(u)
        except Exception:
            continue
        links = {urljoin(u, h).split("#")[0] for h in generic.card_links(generic.soup(page)) + generic.embedded_links(page)}
        if sum(1 for x in links if pattern.search(x)) >= 2:
            out.append(u)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    wait_for_network()
    path = DATA / "site_configs.json"
    manual = {c["site_key"] for c in json.loads((DATA / "site_configs_manual.json").read_text())}
    todo = [c for c in json.loads(path.read_text())
            if c.get("status") in ("ok", "weak", "verified", "verified_auto") and c.get("listing_pattern")
            and c["site_key"] not in manual and c.get("index_source") != "sitemap"
            and (c.get("index_urls") or {}).get("sale") and not (c.get("index_urls") or {}).get("rent")]
    print(f"{len(todo)} sale-only sites", flush=True)
    with ThreadPoolExecutor(args.workers) as ex:
        found = dict(zip((c["site_key"] for c in todo), ex.map(find_rent_pages, todo)))
    by_key = {c["site_key"]: c for c in json.loads(path.read_text())}  # re-read: other tools write it too
    added = 0
    for key, pages in found.items():
        if pages and key in by_key:
            by_key[key].setdefault("index_urls", {})["rent"] = pages
            added += 1
            print(f"{key}: {pages}", flush=True)
    path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=1))
    print(f"rent pages added to {added} of {len(todo)} sites")


if __name__ == "__main__":
    main()
