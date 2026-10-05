"""
Bulk onboarding of agency websites found by recon/find_websites.py:
register → recon → draft config → automatic quality gate → enabled or parked.

The gate dry-runs each draft like scraper.trysite: a site is enabled
(status "verified_auto") only when at least 2 of its sampled detail pages pass
the daily listing gate with a price or "on request", a size and a Côte d'Azur
town. Everything else is parked ("needs_review") with the reason and the
platform fingerprint, for platform adapters / hand configs. Resumable: sites
already in site_configs.json are skipped.

    python recon/onboard.py [--batch 40] [--workers 6]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recon_websites import recon  # noqa: E402
from scraper import generic  # noqa: E402
from scraper.autoconfig import build  # noqa: E402
from scraper.daily import ABROAD, accept  # noqa: E402
from scraper.fetch import Blocked, NotFound, PoliteFetcher  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def gate(cfg: dict, agency: dict) -> tuple[bool, str]:
    """Dry run: a few index pages, 4 sampled details through the daily gate."""
    f = PoliteFetcher(delay=(1, 2.5), block_wait=5, retries=1, accept_404=bool(cfg.get("accept_404")))
    try:
        cards = [c for c in generic.crawl_index(f, {**cfg, "max_pages": 3}) if not ABROAD.search(c["source_url"])]
        if not cards:
            return False, "index: no listing links"
        kept, seen = 0, 0
        for c in random.sample(cards, min(4, len(cards))):
            try:
                html = f.get(c["source_url"])
            except (Blocked, NotFound):
                continue
            seen += 1
            d = accept(generic.parse_detail(html, c["source_url"], cfg, agency, hint=c["transaction_hint"]), html, c["source_url"], cfg)
            if d and (d.get("extra") or {}).get("city"):
                kept += 1
        return kept >= 2, f"{kept}/{seen} samples are real Côte d'Azur listings (index {len(cards)})"
    except (Blocked, NotFound) as e:
        return False, f"blocked: {str(e)[:80]}"
    except Exception as e:  # a bad draft must never stop the batch
        return False, f"error: {type(e).__name__}: {str(e)[:80]}"


def onboard_one(agency: dict) -> dict:
    r = recon(agency)
    if not r.get("ok"):
        return {"recon": r, "cfg": {"site_key": "web-" + host(agency["website"]).replace(".", "-"), "agency": agency["name"],
                                    "website": agency["website"], "status": "unreachable", "runner": "local",
                                    "note": f"onboard: {r.get('error', '')[:120]}"}}
    cfg = build(r, agency)
    cfg.update({"require_riviera": True, "light": False, "runner": cfg.get("runner", "server")})
    if cfg.get("status") in ("ok", "weak") and cfg.get("listing_pattern"):
        ok, why = gate(cfg, agency)
        cfg["status"] = "verified_auto" if ok else "needs_review"
        cfg["note"] = f"onboard gate: {why}; platforms {r.get('platforms')}"
    else:
        cfg["note"] = f"onboard: {cfg.get('status')}; platforms {r.get('platforms')}"
    return {"recon": r, "cfg": cfg}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    found = json.loads((DATA / "discovered_websites.json").read_text())
    agencies = json.loads((DATA / "agencies.json").read_text())
    cfg_path, recon_path = DATA / "site_configs.json", DATA / "website_recon.json"
    configs = json.loads(cfg_path.read_text())
    manual = json.loads((DATA / "site_configs_manual.json").read_text())
    known = {host(a["website"]) for a in agencies if a.get("website")} | {host(c["website"]) for c in configs + manual if c.get("website")}

    todo, names = [], {a["name"] for a in agencies}
    for siren, v in found.items():
        if not v.get("website") or host(v["website"]) in known:
            continue
        known.add(host(v["website"]))
        name = v["name"] if v["name"] not in names else f"{v['name']} ({v['commune']})"
        names.add(name)
        a = {"name": name, "website": v["website"], "region": "cote_azur", "source": "register+search",
             "siren": siren, "commune": v["commune"]}
        agencies.append(a)
        todo.append(a)
    todo = todo[: args.batch]
    print(f"onboarding {len(todo)} new sites")
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(onboard_one, todo))
    # Only the agencies processed this run join agencies.json.
    original = json.loads((DATA / "agencies.json").read_text())
    (DATA / "agencies.json").write_text(json.dumps(original + todo, ensure_ascii=False, indent=1))
    recon_all = json.loads(recon_path.read_text()) + [x["recon"] for x in results]
    recon_path.write_text(json.dumps(recon_all, ensure_ascii=False, indent=1))
    by_key = {c["site_key"]: c for c in configs}
    for x in results:
        by_key[x["cfg"]["site_key"]] = x["cfg"]
    cfg_path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=1))
    print(Counter(x["cfg"]["status"] for x in results))
    for x in results:
        c = x["cfg"]
        print(f"{c['status']:14} {c['agency'][:32]:32} {c.get('website', '')[:40]:40} {c.get('note', '')[:90]}")


if __name__ == "__main__":
    main()
