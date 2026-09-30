"""
Monaco agency site recon (Phase 1 of PLAN.md)
=============================================
One light pass over every agency site in data/monaco_agencies.csv — plain
HTTP, no browser — to decide build order and runner placement:

    - reachable? final URL after redirects, HTTP status
    - anti-bot protection signals (Cloudflare challenge, DataDome, captcha...)
    - site platform / listing-software family (Apimo, WordPress, Next.js...)
      plus every third-party script/iframe domain, so unknown families can be
      spotted by clustering sites that load the same vendor
    - candidate sale / rent index links from the homepage navigation
    - sitemap(s): how many URLs look like individual listings (size estimate)
    - structured data present (JSON-LD, __NEXT_DATA__, __NUXT__)

At most ~5 requests per site. Output: data/site_recon.json + a summary.

Usage:
    python recon/recon_sites.py
    python recon/recon_sites.py --only as-estate.com,abkrealestate.com
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "monaco_agencies.csv"
OUT_PATH = ROOT / "data" / "site_recon.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
TIMEOUT = 20

# substring in page HTML (lower-cased) -> platform family
PLATFORM_SIGNATURES = {
    "apimo": "Apimo",
    "immotoolbox": "Immotoolbox",
    "netty.fr": "Netty",
    "hektor": "Hektor / La Boite Immo",
    "laboiteimmo": "Hektor / La Boite Immo",
    "adaptimmo": "AdaptImmo",
    "ubiflow": "Ubiflow",
    "sweepbright": "SweepBright",
    "realforce": "Realforce",
    "propertybase": "Propertybase",
    "kyero": "Kyero",
    "immo-facile": "Immo-Facile",
    "periclès": "Pericles", "pericles": "Pericles",
    "casafari": "Casafari",
    "idx": None,  # too generic; kept out
    "wp-content": "WordPress",
    "wix.com": "Wix", "wixstatic": "Wix",
    "squarespace": "Squarespace",
    "webflow": "Webflow",
    "shopify": "Shopify",
    "__next_data__": "Next.js", "/_next/": "Next.js",
    "__nuxt": "Nuxt", "/_nuxt/": "Nuxt",
    "drupal": "Drupal",
    "joomla": "Joomla",
    "typo3": "TYPO3",
}

PROTECTION_SIGNATURES = {
    "cf-chl": "cloudflare-challenge", "just a moment...": "cloudflare-challenge",
    "challenges.cloudflare.com": "cloudflare-turnstile",
    "datadome": "datadome", "perimeterx": "perimeterx", "px-captcha": "perimeterx",
    "incapsula": "imperva", "_incap_": "imperva",
    "sucuri": "sucuri", "g-recaptcha": "recaptcha", "hcaptcha": "hcaptcha",
    "captcha": "captcha",
}

SALE_WORDS = ("vente", "acheter", "achat", "a-vendre", "buy", "sale", "for-sale",
              "vendita", "comprare", "kupit", "продажа")
RENT_WORDS = ("location", "louer", "a-louer", "rent", "rental", "lease", "letting",
              "affitto", "arenda", "аренда")
LISTINGS_WORDS = ("properties", "property", "biens", "annonces", "listings",
                  "real-estate", "immobilier", "search", "recherche", "offres", "catalog")
LISTING_URL_RE = re.compile(
    r"/(property|properties|propriete|bien|biens|annonce|annonces|listing|listings|"
    r"vente|location|sale|rent|apartment|appartement|villa|detail|fiche|ref|immobilier)"
    r"[/_-]", re.I)


def fetch(session: requests.Session, url: str) -> requests.Response | None:
    try:
        return session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None


def detect(html_lower: str, table: dict) -> list[str]:
    return sorted({v for k, v in table.items() if v and k in html_lower})


def third_party_domains(soup: BeautifulSoup, own_host: str) -> list[str]:
    hosts = Counter()
    for tag, attr in (("script", "src"), ("iframe", "src"), ("link", "href"), ("img", "src")):
        for el in soup.find_all(tag):
            src = el.get(attr) or ""
            host = urlparse(urljoin(f"https://{own_host}/", src)).hostname or ""
            host = host.removeprefix("www.")
            if host and own_host.removeprefix("www.") not in host:
                hosts[host] += 1
    return [h for h, _ in hosts.most_common(25)]


def nav_links(soup: BeautifulSoup, base: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"sale": [], "rent": [], "listings": []}
    own = urlparse(base).hostname
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        if urlparse(href).hostname != own:
            continue
        hay = (href + " " + a.get_text(" ", strip=True)).lower()
        for key, words in (("sale", SALE_WORDS), ("rent", RENT_WORDS), ("listings", LISTINGS_WORDS)):
            if any(w in hay for w in words) and href not in found[key]:
                found[key].append(href)
    return {k: v[:8] for k, v in found.items()}


def sitemap_listing_count(session: requests.Session, base: str, robots_text: str) -> dict:
    candidates = [urljoin(base, u) for u in re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text or "")]
    candidates += [urljoin(base, "/sitemap.xml"), urljoin(base, "/sitemap_index.xml")]
    seen, urls, fetched = set(), [], 0
    queue = list(dict.fromkeys(candidates))
    while queue and fetched < 6:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        r = fetch(session, sm)
        fetched += 1
        if not r or r.status_code != 200 or "<" not in r.text[:200]:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        if "<sitemapindex" in r.text[:500]:
            # prefer child sitemaps that look like property ones
            locs.sort(key=lambda u: 0 if re.search(r"propert|bien|annonce|listing|estate", u, re.I) else 1)
            queue = locs[:6] + queue
        else:
            urls += locs
    listing_like = [u for u in urls if LISTING_URL_RE.search(urlparse(u).path)]
    return {"sitemaps_read": sorted(seen), "urls_total": len(urls),
            "listing_like": len(listing_like), "listing_samples": listing_like[:5]}


def recon(name: str, url: str) -> dict:
    session = requests.Session()
    rec: dict = {"name": name, "url": url, "host": urlparse(url).hostname}
    t0 = time.time()
    r = fetch(session, url)
    if r is None:
        # retry without www / with www, a common misconfiguration
        host = rec["host"] or ""
        alt = url.replace("://www.", "://") if host.startswith("www.") else url.replace("://", "://www.")
        r = fetch(session, alt)
        if r is not None:
            rec["url_alt_used"] = alt
    if r is None:
        rec.update(reachable=False, error="connection failed / timeout")
        return rec

    # follow a meta-refresh / tiny JS redirect page once
    m = re.search(r"""(?:http-equiv=["']?refresh["']?[^>]*url=|location(?:\.href)?\s*=\s*)["']?([^"'>\s;]+)""", r.text or "", re.I)
    if m and len(r.text or "") < 3000:
        r2 = fetch(session, urljoin(r.url, m.group(1)))
        if r2 is not None:
            rec["meta_redirect"] = r2.url
            r = r2
    html = r.text or ""
    low = html.lower()
    soup = BeautifulSoup(html, "lxml")
    final = r.url
    rec.update(
        reachable=True,
        status=r.status_code,
        final_url=final,
        redirected_offsite=urlparse(final).hostname.removeprefix("www.") != (rec["host"] or "").removeprefix("www."),
        server=r.headers.get("server"),
        cf_ray=bool(r.headers.get("cf-ray")),
        html_bytes=len(html),
        title=(soup.title.get_text(strip=True)[:120] if soup.title else None),
        platforms=detect(low, PLATFORM_SIGNATURES),
        protection=detect(low, PROTECTION_SIGNATURES) if r.status_code in (403, 429, 503) or len(html) < 20000 else
                   [p for p in detect(low, PROTECTION_SIGNATURES) if p not in ("captcha", "recaptcha")],
        generator=(soup.find("meta", attrs={"name": "generator"}) or {}).get("content"),
        json_ld=bool(soup.find("script", type="application/ld+json")),
        next_data=bool(soup.find("script", id="__NEXT_DATA__")),
        nuxt=("__nuxt" in low),
        js_heavy=len(soup.get_text(" ", strip=True)) < 400,
        third_party=third_party_domains(soup, urlparse(final).hostname or ""),
        nav=nav_links(soup, final),
        langs=sorted({(a.get("hreflang") or "").lower() for a in soup.find_all("link", hreflang=True)} - {""}),
    )
    robots = fetch(session, urljoin(final, "/robots.txt"))
    robots_text = robots.text if robots is not None and robots.status_code == 200 else ""
    rec["robots_disallow_all"] = bool(re.search(r"(?im)^user-agent:\s*\*\s*$[\s\S]*?^disallow:\s*/\s*$", robots_text))
    rec["sitemap"] = sitemap_listing_count(session, final, robots_text)
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def classify(rec: dict) -> str:
    """Rough difficulty tier used to order the build and pick a runner."""
    if not rec.get("reachable"):
        return "unreachable"
    if rec.get("status") in (403, 429, 503) or any(p.startswith("cloudflare") or p in ("datadome", "perimeterx", "imperva")
                                                   for p in rec.get("protection", [])):
        return "protected"
    if rec.get("redirected_offsite"):
        return "redirects-offsite"
    if rec.get("js_heavy"):
        return "js-rendered"
    return "open"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated host substrings")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = [(r["Name"].strip(), (r.get("Website") or r.get("URL") or "").strip()) for r in csv.DictReader(f)]
    rows = [r for r in rows if r[1]]
    if args.only:
        wanted = [s.strip() for s in args.only.split(",")]
        rows = [r for r in rows if any(w in r[1] for w in wanted)]

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(recon, n, u): (n, u) for n, u in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            n, u = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:  # never let one site kill the run
                rec = {"name": n, "url": u, "reachable": False, "error": f"{type(e).__name__}: {e}"}
            rec["tier"] = classify(rec)
            results.append(rec)
            print(f"[{i}/{len(rows)}] {rec['tier']:<17} {rec.get('sitemap', {}).get('listing_like', '-'):>5}  {u}",
                  flush=True)

    results.sort(key=lambda r: r["name"].lower())
    OUT_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False))

    tiers = Counter(r["tier"] for r in results)
    plats = Counter(p for r in results for p in r.get("platforms", []))
    vendors = Counter(h for r in results for h in r.get("third_party", []))
    print("\n== tiers", dict(tiers))
    print("== platforms", plats.most_common())
    print("== most shared third-party domains", vendors.most_common(40))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
