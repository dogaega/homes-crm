"""
Decide pending duplicate reviews from the listings' photo galleries.

Two listings of the same flat almost always share several gallery photos
(the owner hands the same set to every agency, or one agency relists its
own); two look-alike flats in one building do not. For each pending review:
dHash up to PHOTOS photos of the listing and of every live source of the
candidate property, then:

  merge   different agencies, ≥3 shared photos or ≥2 covering half of the
          smaller gallery (same rule as the Worker's ingest-time check)
  reject  same agency on both sides, both galleries hashed, nothing shared
          (an agency reuses its own photos when it relists a flat)
  keep    anything else stays for a person — including one agency's shared
          photos, which new developments reuse across different units

The hashes are stored on the sources (/sync/phashes) so later listings are
matched against them at ingest.

Photos that recur across 3+ different properties (agency logo slides,
"Monaco skyline" stock shots, building exteriors) never count as evidence.

    python sync/photo_review.py --dry-run     # report only
    python sync/photo_review.py               # post decisions
    --cache hashes.json                       # reuse hashes between runs (server only)
Env: SYNC_API_BASE_URL, SYNC_API_TOKEN. Nothing is written to disk unless --cache.
"""

from __future__ import annotations

import argparse
import io
import logging
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.fetch import USER_AGENTS  # noqa: E402
from scraper.hero import dhash64  # noqa: E402
from sync.worker_client import WorkerClient  # noqa: E402

log = logging.getLogger("photo_review")
PHOTOS = 6
NEAR = 4           # max differing bits for "same photo" (resize/recompress)
GENERIC_AT = 3     # a photo on this many different properties is not evidence
BANDS = [(0, 13), (13, 26), (26, 39), (39, 52), (52, 64)]  # pigeonhole: ≤4 bits apart → one band equal


class HostPacer:
    """At most PER_HOST requests at a time per host, each slot 0.2–0.6 s apart
    (image CDNs and portal photo servers)."""

    PER_HOST = 3

    def __init__(self):
        self.slots: dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(self.PER_HOST))
        self.session = requests.Session()
        self.session.headers["User-Agent"] = random.choice(USER_AGENTS)

    def get(self, url: str) -> bytes:
        host = urlparse(url).netloc
        with self.slots[host]:
            time.sleep(random.uniform(0.2, 0.6))
            r = self.session.get(url, timeout=20, headers={"Referer": f"https://{host}/"})
        r.raise_for_status()
        return r.content


def hash_url(p: HostPacer, url: str) -> str | None:
    try:
        img = Image.open(io.BytesIO(p.get(url)))
        img.load()
        h = dhash64(img)
    except Exception as e:  # dead image, HTML error page, unsupported format
        log.debug("photo %s: %s", url, e)
        return None
    bits = bin(int(h, 16)).count("1")
    return h if 8 <= bits <= 56 else None  # flat/blank images match everything


def dist(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--cache", help="JSON file of url → hash, read and updated")
    ap.add_argument("--show", type=int, default=0, help="print N sample decisions of each kind")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = WorkerClient()

    reviews: list[dict] = []
    while True:
        page = client.request("GET", f"/sync/reviews?offset={len(reviews)}")
        reviews += page
        if len(page) < 100:
            break
    log.info("%d pending reviews", len(reviews))

    # property id → photo urls (one listing's or a candidate's sources' first PHOTOS each)
    side_urls: dict[str, list[str]] = {}
    for r in reviews:
        side_urls[f"s:{r['source_id']}"] = r["photo_urls"][:PHOTOS]
        for c in r["candidate_sources"]:
            side_urls[f"s:{c['id']}"] = c["photo_urls"][:PHOTOS]
    urls = sorted({u for v in side_urls.values() for u in v if isinstance(u, str) and u.startswith("http")})
    log.info("hashing %d photos", len(urls))
    pacer = HostPacer()
    import json
    hashes: dict[str, str | None] = json.loads(Path(args.cache).read_text()) if args.cache and Path(args.cache).exists() else {}
    todo = [u for u in urls if u not in hashes]
    log.info("%d already hashed (cache)", len(urls) - len(todo))
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (u, h) in enumerate(zip(todo, ex.map(lambda u: hash_url(pacer, u), todo)), 1):
            hashes[u] = h
            if i % 500 == 0:
                log.info("  %d/%d hashed", i, len(todo))
                if args.cache:
                    Path(args.cache).write_text(json.dumps(hashes))
    if args.cache:
        Path(args.cache).write_text(json.dumps(hashes))

    # Which property each source side belongs to (generic-photo detection counts properties).
    prop_of: dict[str, str] = {}
    for r in reviews:
        prop_of[f"s:{r['source_id']}"] = r["source_property_id"]
        for c in r["candidate_sources"]:
            prop_of[f"s:{c['id']}"] = r["candidate_property_id"]
    side_hashes = {k: [hashes[u] for u in v if hashes.get(u)] for k, v in side_urls.items()}
    owners: dict[str, set[str]] = defaultdict(set)  # hash → properties
    for k, hs in side_hashes.items():
        for h in hs:
            owners[h].add(prop_of[k])
    bands: dict[tuple[int, int], list[str]] = defaultdict(list)
    for h in owners:
        v = int(h, 16)
        for i, (a, b) in enumerate(BANDS):
            bands[(i, (v >> (64 - b)) & ((1 << (b - a)) - 1))].append(h)
    generic: set[str] = set()
    for h in owners:
        v = int(h, 16)
        props = set(owners[h])
        for i, (a, b) in enumerate(BANDS):
            for o in bands[(i, (v >> (64 - b)) & ((1 << (b - a)) - 1))]:
                if o != h and dist(h, o) <= NEAR:
                    props |= owners[o]
        if len(props) >= GENERIC_AT:
            generic.add(h)
    log.info("%d distinct photo hashes, %d generic", len(owners), len(generic))

    decisions: list[dict] = []
    tally: Counter = Counter()
    for r in reviews:
        mine = [h for h in side_hashes[f"s:{r['source_id']}"] if h not in generic]
        theirs = [h for c in r["candidate_sources"] for h in side_hashes[f"s:{c['id']}"] if h not in generic]
        shared = sum(1 for h in mine if any(dist(h, o) <= NEAR for o in theirs))
        same_agency = any(c["agency_id"] == r["agency_id"] for c in r["candidate_sources"])
        smaller = min(len(mine), len(theirs))
        if not same_agency and (shared >= 3 or (shared >= 2 and shared * 2 >= smaller)):
            decisions.append({"id": r["id"], "decision": "merge", "evidence": f"photos_shared:{shared}"})
            tally["merge"] += 1
        elif shared == 0 and same_agency and len(mine) >= 3 and len(theirs) >= 3:
            decisions.append({"id": r["id"], "decision": "reject", "evidence": "photos_none_same_agency"})
            tally["reject"] += 1
        else:
            tally["keep (shared=%d%s)" % (min(shared, 2), ", no photos" if not smaller else "")] += 1
    log.info("decisions: %s", dict(tally))
    if args.show:
        by = {r["id"]: r for r in reviews}
        for kind in ("merge", "reject"):
            for d in [d for d in decisions if d["decision"] == kind][:args.show]:
                r = by[d["id"]]
                print(kind, d["evidence"], r["reasons"], r["site_key"], "→", [c["site_key"] for c in r["candidate_sources"]],
                      r["photo_urls"][:1], [c["photo_urls"][:1] for c in r["candidate_sources"]][:1])
    if args.dry_run:
        return
    # Store every fingerprint (generic ones included: the Worker compares
    # galleries pair by pair, after field matching).
    items = [{"source_id": k[2:], "phashes": hs} for k, hs in side_hashes.items() if hs]
    stored = sum(client.request("POST", "/sync/phashes", {"items": items[i:i + 100]})["updated"]
                 for i in range(0, len(items), 100))
    log.info("stored gallery hashes for %d sources", stored)
    res: Counter = Counter()
    for i in range(0, len(decisions), 25):
        for o in client.request("POST", "/sync/reviews/decide", {"decisions": decisions[i:i + 25]})["results"]:
            res[o.get("status") or o.get("skipped") or o.get("error")] += 1
    log.info("posted: %s", dict(res))


if __name__ == "__main__":
    main()
