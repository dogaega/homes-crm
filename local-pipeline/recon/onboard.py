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
import re
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
from sync.netwait import online, wait_for_network  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def gate(cfg: dict, agency: dict) -> tuple[bool, str]:
    """Dry run: a few index pages, 4 sampled details through the daily gate."""
    if cfg.get("render"):
        from scraper.browser import BrowserFetcher
        f = BrowserFetcher(delay=(1, 2.5), block_wait=5, retries=1)
    else:
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
    finally:
        if cfg.get("render"):
            f.close()


# Listing platforms shared by many small agencies: one template each.
def platform_config(platforms: list[str], site: str) -> dict | None:
    h = re.escape(host(site))
    root = f"https://{urlparse(site).netloc}"
    if "hektor" in platforms:  # La Boîte Immo / Hektor: /vente/N, /location/N
        return {"index_urls": {"sale": [f"{root}/vente/{n}" for n in range(1, 16)],
                               "rent": [f"{root}/location/{n}" for n in range(1, 9)]},
                "listing_pattern": rf"^https?://(?:www\.)?{h}/(?:vente|location)/(?:[^/?#]+/)+\d+-[a-z0-9-]+/?$",
                "max_pages": 1, "platform_adapter": "hektor"}
    if "apimo" in platforms:
        # Apimo themes: /fr/ventes or /fr/nos-biens; details /fr/propriete/vente+type+town+…+id
        # or /fr/propriete/vente/type/town/slug/id.
        return {"index_urls": {"sale": [f"{root}/fr/ventes", f"{root}/fr/nos-biens", f"{root}/fr/acheter"]
                                       + [f"{root}/fr/ventes?page={n}" for n in range(2, 12)]
                                       + [f"{root}/fr/nos-biens?page={n}" for n in range(2, 12)],
                               "rent": [f"{root}/fr/locations", f"{root}/fr/louer"] + [f"{root}/fr/locations?page={n}" for n in range(2, 6)]},
                "listing_pattern": rf"^https?://(?:www\.)?{h}/(?:fr|en)/propriete/(?:vente|location)(?:\+[^/?#]+\+|/(?:[^/?#]+/)+)\d{{6,}}/?$",
                "max_pages": 1, "platform_adapter": "apimo"}
    if "netty" in platforms:  # Netty / modelo: grid in a JSON blob, details at /vente|location/<slug>,<ref>
        return {"index_urls": {"sale": [f"{root}/vente"] + [f"{root}/vente?page={n}" for n in range(2, 12)],
                               "rent": [f"{root}/location"] + [f"{root}/location?page={n}" for n in range(2, 6)]},
                "listing_pattern": rf"^https?://(?:www\.)?{h}/(?:vente|location)/[a-z0-9-]+,[A-Z]{{1,3}}\d+$",
                "max_pages": 1, "platform_adapter": "netty"}
    if "twimmo" in platforms:  # Twimmo: cards link by data-lien to /vente-…-1-428v1m.html
        sale = ["vente-appartement", "vente-maison", "vente-terrain", "vente"]
        return {"index_urls": {"sale": [f"{root}/{p}.html" for p in sale] + [f"{root}/{p}-{n}.html" for p in sale[:2] for n in range(2, 9)],
                               "rent": [f"{root}/toutes-locations.html", f"{root}/location-appartement,maison.html"]
                                       + [f"{root}/toutes-locations-{n}.html" for n in range(2, 5)]},
                "listing_pattern": rf"^https?://(?:www\.)?{h}/(?:en/)?(?:vente|location|sale|rent)[^/?#]*-\d+-\d+[a-z]+\d+[a-z]*\.html$",
                "max_pages": 1, "accept_404": True, "platform_adapter": "twimmo"}
    if any(p.startswith("wp-") for p in platforms) or "wordpress" in platforms:
        # WordPress property themes (Houzez, WPResidence, RealHomes, Estatik …): one post type per
        # listing, every one of them in the theme's sitemap.
        return {"index_source": "sitemap",
                "listing_pattern": rf"^https?://(?:www\.)?{h}/(?:[a-z]{{2}}/)?(?:property|properties|propriete|proprietes|"
                                   rf"bien|biens|annonce|annonces|listing|listings|estate_property|real-estate|immobilier)/[^/?#]+/?$",
                "platform_adapter": "wordpress"}
    return None


def onboard_one(agency: dict) -> dict:
    try:
        return _onboard_one(agency)
    except Exception as e:  # one malformed site must never stop a batch
        return {"recon": {"name": agency["name"], "ok": False, "error": f"{type(e).__name__}: {e}"},
                "cfg": {"site_key": "web-" + host(agency["website"]).replace(".", "-"), "agency": agency["name"],
                        "website": agency["website"], "status": "bad_config", "runner": "local",
                        "note": f"onboard crashed: {type(e).__name__}: {str(e)[:100]}"}}


def _onboard_one(agency: dict) -> dict:
    r = recon(agency)
    # The Mac's own connection (hotspot) dropping is not the site being down.
    for _ in range(3):
        if r.get("ok") or online():
            break
        wait_for_network()
        r = recon(agency)
    if not r.get("ok"):
        return {"recon": r, "cfg": {"site_key": "web-" + host(agency["website"]).replace(".", "-"), "agency": agency["name"],
                                    "website": agency["website"], "status": "unreachable", "runner": "local",
                                    "note": f"onboard: {r.get('error', '')[:120]}"}}
    cfg = build(r, agency)
    cfg.update({"require_riviera": True, "light": False, "runner": cfg.get("runner", "server")})
    why = cfg.get("status")
    if cfg.get("status") in ("ok", "weak") and cfg.get("listing_pattern"):
        ok, why = gate(cfg, agency)
        if ok:
            cfg.update(status="verified_auto", note=f"onboard gate: {why}; platforms {r.get('platforms')}")
            return {"recon": r, "cfg": cfg}
    # The generic draft failed: try the site's platform template.
    tpl = platform_config(r.get("platforms") or [], agency["website"])
    if tpl:
        alt = {**cfg, **tpl}
        ok, why2 = gate(alt, agency)
        if ok:
            alt.update(status="verified_auto", note=f"onboard gate ({tpl['platform_adapter']} adapter): {why2}")
            return {"recon": r, "cfg": alt}
        why = f"{why}; {tpl['platform_adapter']} adapter: {why2}"
    cfg.update(status="needs_review", note=f"onboard: {why}; platforms {r.get('platforms')}")
    return {"recon": r, "cfg": cfg}


def onboard_rendered(site_keys: list[str]) -> None:
    """Parked sites whose listing grid is drawn by JavaScript: draft and gate
    them in headless Chromium (Mac runner only)."""
    agencies = {a["name"]: a for a in json.loads((DATA / "agencies.json").read_text())}
    cfg_path = DATA / "site_configs.json"
    todo = [c for c in json.loads(cfg_path.read_text()) if c["site_key"] in set(site_keys) and c["agency"] in agencies]
    print(f"rendering {len(todo)} sites")
    for c in todo:
        agency = agencies[c["agency"]]
        try:
            r = recon(agency)
            cfg = build(r, agency, render=True) if r.get("ok") else None
        except Exception as e:
            print(f"error      {c['website']}: {type(e).__name__}: {str(e)[:80]}")
            continue
        if not cfg or not cfg.get("listing_pattern"):
            print(f"no links   {c['website']}")
            continue
        cfg.update({"require_riviera": True, "light": False, "runner": "local"})
        ok, why = gate(cfg, agency)
        cfg.update(status="verified_auto" if ok else "needs_review", note=f"onboard (rendered): {why}")
        by_key = {x["site_key"]: x for x in json.loads(cfg_path.read_text())}
        by_key[c["site_key"]] = cfg if ok else {**c, "note": f"{c.get('note', '')}; rendered: {why} [rendered]"}
        cfg_path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=1))
        print(f"{cfg['status']:14} {c['website']}: {why}", flush=True)


def retry(workers: int, regate: bool = False, only: list[str] | None = None) -> None:
    """Re-onboard sites recorded as unreachable (connection errors), or with
    --regate the parked sites on a platform that now has an adapter."""
    agencies = {a["name"]: a for a in json.loads((DATA / "agencies.json").read_text())}
    cfg_path = DATA / "site_configs.json"
    if only:
        todo = [c for c in json.loads(cfg_path.read_text()) if c["site_key"] in set(only) and c["agency"] in agencies
                and c.get("status") != "verified_auto"]
    elif regate:
        todo = [c for c in json.loads(cfg_path.read_text()) if c.get("status") == "needs_review" and c["agency"] in agencies
                and re.search(r"'(?:netty|apimo|wordpress|wp-[a-z]+)'", c.get("note") or "")
                and "[regated]" not in (c.get("note") or "")]
    else:
        todo = [c for c in json.loads(cfg_path.read_text()) if c.get("status") == "unreachable" and c["agency"] in agencies
                and re.search(r"ConnectionError|Timeout", c.get("note") or "") and "[retried]" not in (c.get("note") or "")]
    print(f"retrying {len(todo)} {'parked' if regate else 'unreachable'} sites")
    total: Counter = Counter()
    for i in range(0, len(todo), 60):  # saved per chunk: an interrupted run keeps what it did
        wait_for_network()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda c: onboard_one(agencies[c["agency"]]), todo[i:i + 60]))
        by_key = {c["site_key"]: c for c in json.loads(cfg_path.read_text())}
        for x in results:
            if regate and x["cfg"]["status"] == "needs_review":
                x["cfg"]["note"] = f"{x['cfg'].get('note', '')} [regated]"  # checked with today's adapters
            if not regate and x["cfg"]["status"] == "unreachable":
                x["cfg"]["note"] = f"{x['cfg'].get('note', '')} [retried]"  # down twice, online both times: dead
            by_key[x["cfg"]["site_key"]] = x["cfg"]
        cfg_path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=1))
        total.update(x["cfg"]["status"] for x in results)
        print(f"{min(i + 60, len(todo))}/{len(todo)}", dict(total), flush=True)
    print(total)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--retry-unreachable", action="store_true",
                    help="re-onboard sites recorded as unreachable (connection errors / timeouts)")
    ap.add_argument("--regate", action="store_true", help="re-onboard parked sites on platforms with an adapter")
    ap.add_argument("--regate-sites", help="file with site_keys to re-onboard with today's adapters")
    ap.add_argument("--render-sites", help="file with site_keys of parked JS-rendered sites to onboard in Chromium")
    args = ap.parse_args()
    if args.render_sites:
        onboard_rendered(Path(args.render_sites).read_text().split())
        return
    if args.regate_sites:
        retry(args.workers, regate=True, only=Path(args.regate_sites).read_text().split())
        return
    if args.retry_unreachable or args.regate:
        retry(args.workers, regate=args.regate)
        return
    # Every finder writes its own discovered_*.json (search, guesses, OSM, FNAIM …).
    found = {}
    for f in sorted(DATA.glob("discovered_*.json")):
        found.update(json.loads(f.read_text()))
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
    # Re-read just before writing: other tools may have changed the file meanwhile.
    by_key = {c["site_key"]: c for c in json.loads(cfg_path.read_text())}
    for x in results:
        by_key[x["cfg"]["site_key"]] = x["cfg"]
    cfg_path.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=1))
    print(Counter(x["cfg"]["status"] for x in results))
    for x in results:
        c = x["cfg"]
        print(f"{c['status']:14} {c['agency'][:32]:32} {c.get('website', '')[:40]:40} {c.get('note', '')[:90]}")


if __name__ == "__main__":
    main()
