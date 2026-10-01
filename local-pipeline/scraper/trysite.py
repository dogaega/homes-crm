"""
Dry-run one agency-site config: crawl its index and parse a few detail pages
exactly as the daily run would, print what would be pushed. Nothing is sent.

    python -m scraper.trysite web-westrope-monaco-com            # stored config
    python -m scraper.trysite web-x-mc --config cand.json -n 6   # candidate config (JSON object)
    python -m scraper.trysite web-x-mc --render                  # force headless Chromium

Exit code 0 when the index found listings and at least one sample passed
the listing gate, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper import generic  # noqa: E402
from scraper.daily import accept, agencies, load_site_configs  # noqa: E402
from scraper.fetch import PoliteFetcher  # noqa: E402

FIELDS = ("transaction_type", "price", "price_on_request", "living_area_sqm", "rooms", "bedrooms", "floor",
          "external_ref", "quarter", "building_name", "agent_name", "agent_phone", "agent_email")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site_key")
    ap.add_argument("--config", help="JSON file with a candidate config (merged over the stored one)")
    ap.add_argument("-n", type=int, default=4, help="detail pages to sample")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    cfg = next((c for c in load_site_configs() if c["site_key"] == args.site_key), {"site_key": args.site_key})
    if args.config:
        cfg = {**cfg, **json.loads(Path(args.config).read_text())}
    if args.render:
        cfg["render"] = True
    agency = next((a for a in agencies(None) if a["name"] == cfg.get("agency")), {"name": cfg.get("agency")})
    if cfg.get("render"):
        from scraper.browser import BrowserFetcher
        f = BrowserFetcher(delay=(1, 2))
    else:
        f = PoliteFetcher(delay=(1, 2), block_wait=5, retries=1)
    try:
        cards = generic.crawl_index(f, cfg)
        from scraper.autoconfig import OUTSIDE
        cards = [c for c in cards if not OUTSIDE.search(c["source_url"])]
        print(f"index: {len(cards)} listing URLs  "
              f"(sale {sum(c['transaction_hint'] == 'sale' for c in cards)}, rent {sum(c['transaction_hint'] == 'rent' for c in cards)})")
        for c in cards[:5]:
            print("  ", c["source_url"])
        ok = 0
        for c in random.sample(cards, min(args.n, len(cards))):
            u = c["source_url"]
            try:
                html = f.get(u)
                d = generic.parse_detail(html, u, cfg, agency, hint=c["transaction_hint"])
                kept = accept(dict(d), html, u, cfg)
            except Exception as e:
                print(f"\n{u}\n   ERROR {e!r}"[:300])
                continue
            ok += kept is not None
            print(f"\n{u}\n   {'KEPT' if kept else 'DROPPED'}  title={str(d.get('title'))[:70]!r}  photos={len(d.get('photo_urls') or [])}")
            print("   " + "  ".join(f"{k}={d.get(k)!r}" for k in FIELDS if d.get(k) not in (None, False, "")))
        print(f"\nsamples kept: {ok}/{min(args.n, len(cards))}")
        sys.exit(0 if cards and ok else 1)
    finally:
        if hasattr(f, "close"):
            f.close()


if __name__ == "__main__":
    main()
