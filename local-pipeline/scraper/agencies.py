"""
Master agency list → data/agencies.json
========================================
Merges three sources into one record per real agency:
  - CIM members        data/cim_agencies.json   (python -m scraper.cim agencies)
  - MCRE portal        data/mcre_agencies.json  (python -m scraper.mcre agencies)
  - Official directory annuaire-monaco.mc (fetched here) — catches licensed
    agencies on neither portal.

Replaces the old data/monaco_agencies.csv (80% invented domains).

Usage:
    python -m scraper.agencies            # fetch directory + merge
    python -m scraper.agencies --no-fetch # merge only (uses data/directory_agencies.json)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.fetch import PoliteFetcher  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
DIRECTORY = "https://annuaire-monaco.mc/Agences-Immobilieres-monaco"

STOP = {"monaco", "real", "estate", "estates", "immobilier", "immobiliere", "immobiliers", "agence", "agency",
        "properties", "property", "sam", "sarl", "surl", "sa", "the", "de", "la", "le", "les", "mc", "immo",
        "group", "groupe", "international", "and", "et", "realty", "services", "transactions", "gestion",
        "cabinet", "monte", "carlo", "by", "luxury", "family"}


def tokens(name: str) -> frozenset[str]:
    n = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b([a-z])\.(?=[a-z]\.?)", r"\1", n).replace(".", "").replace("&", " ")
    return frozenset(w for w in re.sub(r"[^a-z0-9 ]", " ", n).split() if w not in STOP and len(w) > 1)


def same_agency(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return a.strip().lower() == b.strip().lower()
    return ta <= tb or tb <= ta or len(ta & tb) >= 2


def fetch_directory(f: PoliteFetcher) -> list[dict]:
    out: dict[str, dict] = {}
    page, last = 1, 1
    while page <= last:
        html = f.get(f"{DIRECTORY}?page={page}")
        last = max([last] + [int(x) for x in re.findall(r"\?page=(\d+)", html)])
        for it in BeautifulSoup(html, "lxml").select(".list__item"):
            n = it.select_one(".name") or it.select_one('a[href^="/"]')
            cat = it.select_one(".cat")
            name = n.get_text(" ", strip=True) if n else None
            if not name or name in out or (cat and "Agences Immobili" not in cat.get_text()):
                continue
            addr = next((a.get_text(" ", strip=True) for a in it.select("a.info")
                         if not a.get("href", "").startswith(("tel:", "http", "mailto"))), None)
            out[name] = {"name": name, "address": addr,
                         "phones": [a["href"][4:] for a in it.select('a[href^="tel:"]')]}
        page += 1
    return list(out.values())


def merge(cim: list[dict], mcre: list[dict], directory: list[dict]) -> list[dict]:
    agencies: list[dict] = []
    for c in cim:
        agencies.append({
            "name": c["name"], "cim_member": True, "cim_slug": c["cim_slug"], "manager": c.get("manager"),
            "address": c.get("address"), "phone": c.get("phone"), "email": c.get("email"),
            "website": c.get("website"), "cim_listing_count": c.get("cim_listing_count"),
        })

    def find(name: str) -> dict | None:
        return next((a for a in agencies if same_agency(a["name"], name)), None)

    for m in mcre:
        if not m.get("name"):
            continue
        a = find(m["name"])
        if a is None:
            a = {"name": m["name"], "cim_member": False}
            agencies.append(a)
        a.update({"mcre_slug": m["mcre_slug"], "mcre_tc": m["mcre_tc"], "mcre_listing_count": m.get("mcre_listing_count")})

    for d in directory:
        a = find(d["name"])
        if a is None:
            a = {"name": d["name"], "cim_member": False}
            agencies.append(a)
        a["in_official_directory"] = True
        a.setdefault("address", d.get("address"))
        if not a.get("phone") and d.get("phones"):
            a["phone"] = d["phones"][0]

    for a in agencies:
        a["covered_by"] = [s for s, k in (("cim", "cim_listing_count"), ("mcre", "mcre_listing_count")) if a.get(k)]
    agencies.sort(key=lambda a: -max(a.get("cim_listing_count") or 0, a.get("mcre_listing_count") or 0))
    return agencies


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    dpath = DATA / "directory_agencies.json"
    if args.no_fetch:
        directory = json.loads(dpath.read_text())
    else:
        directory = fetch_directory(PoliteFetcher(delay=(2, 4), block_wait=10))
        dpath.write_text(json.dumps(directory, ensure_ascii=False, indent=1))
    cim = json.loads((DATA / "cim_agencies.json").read_text())
    mcre = json.loads((DATA / "mcre_agencies.json").read_text())
    agencies = merge(cim, mcre, directory)
    (DATA / "agencies.json").write_text(json.dumps(agencies, ensure_ascii=False, indent=1))
    uncovered = [a["name"] for a in agencies if not a["covered_by"]]
    print(f"{len(agencies)} agencies: {sum(a.get('cim_member', False) for a in agencies)} CIM members, "
          f"{sum(1 for a in agencies if a.get('mcre_slug'))} on MCRE, {len(uncovered)} with no portal listings")
    print("no portal coverage:", uncovered)


if __name__ == "__main__":
    main()
