"""
Recon of every agency's own website (data/agencies.json `website`):
platform fingerprint, sale/rent index candidates, sample listing URLs,
whether listing pages show an individual agent, sitemap. Prints only a
summary; full results → data/website_recon.json.

    python recon/recon_websites.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.fetch import Blocked, NotFound, PoliteFetcher  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"

PLATFORMS = [
    ("apimo", r"apimo\.(?:net|com)|apimo-|/apimo/"),
    ("immotoolbox", r"immotoolbox|itb-|immo-toolbox"),
    ("colibri", r"colibri-antispam|analytics\.colibri\.mc|monacodigital"),
    ("zebrasoft", r"zebrasoft"),
    ("immosoft", r"immosoft"),
    ("ubiflow", r"ubiflow"),
    ("netty", r"netty\.fr|nettytools"),
    ("hektor", r"hektor|la-boite-immo|laboiteimmo"),
    ("adaptimmo", r"adaptimmo"),
    ("wp-houzez", r"houzez"),
    ("wp-realhomes", r"realhomes|inspiry"),
    ("wp-estatik", r"estatik|es-property"),
    ("wp-wpresidence", r"wpresidence|wpestate"),
    ("wp-easy-property", r"easy-property-listings|epl-"),
    ("wordpress", r"wp-content|wp-includes"),
    ("nextjs", r"__NEXT_DATA__|/_next/"),
    ("nuxt", r"__NUXT__|/_nuxt/"),
    ("webflow", r"webflow"),
    ("wix", r"wix\.com|wixstatic"),
    ("squarespace", r"squarespace"),
    ("drupal", r"drupal"),
    ("prestimmo", r"prestimmo"),
]
INDEX_HINTS = re.compile(r"(vente|ventes|acheter|achat|buy|sale|sales|for-sale|location|locations|louer|rent|rental|"
                         r"rentals|to-let|annonces|biens|properties|property|listings|immobilier|nos-biens|offres)",
                         re.I)
LISTING_HINTS = re.compile(r"/(?:bien|biens|property|properties|annonce|annonces|listing|listings|detail|fiche|"
                           r"immobile|appartement|apartment|vente|location|sale|rent)[s]?/[^?#]*\d{2,}", re.I)
AGENT_HINTS = re.compile(r"(votre\s+(?:contact|conseill[eè]re?|agent)|your\s+(?:contact|agent|advisor|consultant)|"
                         r"agent\s+in\s+charge|n[ée]gociat|contact\s+person|conseiller|consultant|"
                         r"property\s+advisor|responsable\s+du\s+bien)", re.I)


def fingerprint(html: str) -> list[str]:
    return [name for name, rx in PLATFORMS if re.search(rx, html, re.I)]


def same_host(url: str, base: str) -> bool:
    return urlparse(url).netloc.removeprefix("www.") == urlparse(base).netloc.removeprefix("www.")


def links(html: str, base: str) -> list[str]:
    out = []
    for h in re.findall(r'href="([^"#]+)"', html):
        u = urljoin(base, h)
        if u.startswith("http") and same_host(u, base) and u not in out:
            out.append(u)
    return out


def recon(agency: dict) -> dict:
    site = agency["website"]
    f = PoliteFetcher(delay=(1.5, 3), block_wait=10, retries=1, timeout=25)
    r = {"name": agency["name"], "website": site, "ok": False}
    try:
        home = f.get(site)
    except (Blocked, NotFound) as e:
        r["error"] = str(e)[:200]
        return r
    except Exception as e:
        r["error"] = repr(e)[:200]
        return r
    r["ok"] = True
    r["platforms"] = fingerprint(home)
    r["js_rendered"] = len(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", "", home, flags=re.S).strip()) < 800
    ls = links(home, site)
    idx = [u for u in ls if INDEX_HINTS.search(urlparse(u).path)][:15]
    r["index_candidates"] = idx
    listing = [u for u in ls if LISTING_HINTS.search(urlparse(u).path)]
    # One index page usually exposes listing links when the home page doesn't.
    for u in idx[:3]:
        if len(listing) >= 5:
            break
        try:
            h = f.get(u)
            r["platforms"] = sorted(set(r["platforms"]) | set(fingerprint(h)))
            listing += [x for x in links(h, site) if LISTING_HINTS.search(urlparse(x).path) and x not in listing]
        except Exception:
            continue
    r["listing_samples"] = listing[:5]
    r["listing_links_seen"] = len(listing)
    if listing:
        try:
            h = f.get(listing[0])
            text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", h, flags=re.S)
            r["agent_hint"] = bool(AGENT_HINTS.search(text))
            r["detail_tel_links"] = len(set(re.findall(r'href="tel:([^"]+)"', h)))
            r["detail_mailto"] = len(set(re.findall(r'href="mailto:([^"]+)"', h)))
            r["jsonld_types"] = sorted(set(re.findall(r'"@type"\s*:\s*"([A-Za-z]+)"', h)))[:8]
        except Exception as e:
            r["detail_error"] = repr(e)[:120]
    try:
        sm = f.get(urljoin(site, "/sitemap.xml"))
        r["sitemap"] = "<urlset" in sm or "<sitemapindex" in sm
    except Exception:
        r["sitemap"] = False
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--new", action="store_true", help="only agencies not in website_recon.json yet; merge results")
    args = ap.parse_args()
    agencies = [a for a in json.loads((DATA / "agencies.json").read_text()) if a.get("website")]
    previous = json.loads((DATA / "website_recon.json").read_text()) if args.new else []
    if args.new:
        done = {r["name"] for r in previous}
        agencies = [a for a in agencies if a["name"] not in done]
    if args.limit:
        agencies = agencies[: args.limit]
    # Different hosts, so a few in parallel is still polite per site.
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(recon, agencies))
    (DATA / "website_recon.json").write_text(json.dumps(previous + results, ensure_ascii=False, indent=1))
    ok = [r for r in results if r["ok"]]
    print(f"{len(results)} sites, {len(ok)} reachable")
    print("platforms:", Counter(p for r in ok for p in (r.get("platforms") or ["unknown"])).most_common())
    print("with listing links:", sum(1 for r in ok if r.get("listing_links_seen")),
          "| agent hint:", sum(1 for r in ok if r.get("agent_hint")),
          "| js-rendered:", sum(1 for r in ok if r.get("js_rendered")),
          "| sitemap:", sum(1 for r in ok if r.get("sitemap")))
    print("unreachable:", [(r["name"], r.get("error", "")[:60]) for r in results if not r["ok"]])


if __name__ == "__main__":
    main()
