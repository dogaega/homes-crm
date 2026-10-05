"""
Import agency lists (CSV: name + website URL columns) into data/agencies.json:
dedupe by domain against what we already have, set portals / holiday-rental
platforms aside, and keep only domains that answer with a real site (not
parked, not "domain for sale"). Prints a summary; candidates and verdicts go to
data/import_candidates.json.

    python recon/import_lists.py ~/Downloads/a.csv ~/Downloads/b.csv [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests

DATA = Path(__file__).resolve().parents[1] / "data"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

# Portals, classifieds and holiday-rental platforms: not agency websites.
PLATFORM = re.compile(r"seloger|logic-?immo|bienici|leboncoin|pap\.fr|green-?acres|rightmove|kyero|idealista|jamesedition|"
                      r"airbnb|booking\.com|vrbo|abritel|homeaway|tripadvisor|properstar|immoweb|zillow|realtor\.com|"
                      r"luxuryestate|lux-residence|belles-?demeures|proprietes\.lefigaro|figaro|avendrealouer|paruvendu|"
                      r"ouestfrance-immo|superimmo|meilleursagents|explorimmo|locservice|lodgis|spotahome|housinganywhere|"
                      r"facebook|instagram|linkedin|google|wikipedia|youtube", re.I)
PARKED = re.compile(r"domain (?:is )?for sale|buy this domain|ce domaine est (?:à vendre|en vente)|parked (?:free|domain)|"
                    r"sedoparking|godaddy\.com/forsale|dan\.com|afternic|hugedomains|this domain (?:may be|is) for sale|"
                    r"domaine en vente|site en construction|under construction|coming soon", re.I)


def host(url: str) -> str:
    u = url.strip()
    if not u.startswith("http"):
        u = "https://" + u
    return urlparse(u).netloc.lower().removeprefix("www.").split(":")[0]


def read_csv(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        name = next((r[k] for k in r if k and re.search(r"name|agence|agency|platform", k, re.I) and r[k]), None)
        url = next((r[k] for k in r if k and re.search(r"url|website|site|web", k, re.I) and r[k]), None)
        cat = next((r[k] for k in r if k and re.search(r"categ", k, re.I) and r[k]), "")
        if name and url and "." in url:
            out.append({"name": name.strip(), "website": url.strip(), "category": cat.strip(), "source": path.name})
    return out


def check(c: dict) -> dict:
    url = c["website"] if c["website"].startswith("http") else "https://" + c["website"]
    for attempt in (url, url.replace("https://", "http://")):
        try:
            r = requests.get(attempt, timeout=15, headers={"User-Agent": UA}, allow_redirects=True)
            text = r.text[:200_000]
            final = host(r.url)
            if r.status_code >= 400 and r.status_code not in (401, 403):
                return {**c, "live": False, "why": f"HTTP {r.status_code}"}
            if PARKED.search(text) and len(re.findall(r"<a\s", text, re.I)) < 25:
                return {**c, "live": False, "why": "parked / for sale / under construction"}
            if PLATFORM.search(final):
                return {**c, "live": False, "why": f"redirects to platform {final}"}
            return {**c, "live": True, "final_url": r.url, "final_host": final, "status": r.status_code,
                    "links": len(re.findall(r"<a\s", text, re.I))}
        except requests.RequestException as e:
            err = type(e).__name__
    return {**c, "live": False, "why": err}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    agencies = json.loads((DATA / "agencies.json").read_text())
    known = {host(a["website"]) for a in agencies if a.get("website")}
    for f in ("site_configs.json", "site_configs_manual.json"):
        known |= {host(c["website"]) for c in json.loads((DATA / f).read_text()) if c.get("website")}

    cands: dict[str, dict] = {}
    platforms: list[dict] = []
    for p in args.csv:
        for c in read_csv(Path(p).expanduser()):
            h = host(c["website"])
            if not h or h in known:
                continue
            if PLATFORM.search(h) or re.search(r"portal|aggregat|vacation|short-term|seasonal|peer-to-peer", c["category"], re.I):
                platforms.append(c)
                continue
            cands.setdefault(h, c)
    print(f"{len(cands)} new agency domains to check; {len(platforms)} portals/platforms set aside")
    with ThreadPoolExecutor(max_workers=24) as ex:
        results = list(ex.map(check, cands.values()))
    live = [r for r in results if r["live"]]
    # Several list entries can land on the same site after redirects.
    by_final: dict[str, dict] = {}
    for r in live:
        if r["final_host"] not in known:
            by_final.setdefault(r["final_host"], r)
    print(f"live: {len(by_final)} distinct sites | dead: {len(results) - len(live)}",
          Counter(r["why"].split(" ")[0] for r in results if not r["live"]).most_common(6))
    (DATA / "import_candidates.json").write_text(json.dumps(
        {"live": list(by_final.values()), "dead": [r for r in results if not r["live"]], "platforms": platforms},
        ensure_ascii=False, indent=1))
    if args.dry_run:
        return
    names = {a["name"] for a in agencies}
    for r in by_final.values():
        name = r["name"] if r["name"] not in names else f"{r['name']} ({r['final_host']})"
        agencies.append({"name": name, "website": f"https://{r['final_host']}/", "region": "cote_azur",
                         "source": r["source"], "list_category": r["category"]})
        names.add(name)
    (DATA / "agencies.json").write_text(json.dumps(agencies, ensure_ascii=False, indent=1))
    print(f"agencies.json: +{len(by_final)} → {len(agencies)}")


if __name__ == "__main__":
    sys.exit(main())
