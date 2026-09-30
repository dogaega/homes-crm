"""
Website discovery for every agency → fills `website` in data/agencies.json
and maps the original 255-name CSV onto real agencies.

Sources, in order: CIM listing pages (already in agencies.json), MCRE agency
pages ("Website:"), CSV domains that the recon proved reachable.

    python -m scraper.websites          # -> data/agencies.json (+ data/csv_mapping.json)
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.agencies import match_score  # noqa: E402
from scraper.fetch import PoliteFetcher  # noqa: E402

log = logging.getLogger("websites")
DATA = Path(__file__).resolve().parents[1] / "data"
MCRE = "https://www.montecarlo-realestate.com/en/estate-agents/"

# Portals and social sites are never an agency's own website.
NOT_OWN = re.compile(r"facebook|instagram|linkedin|pinterest|twitter|youtube|google|montecarlo-realestate|"
                     r"chambre-immobiliere|jamesedition|rightmove|properstar|idealista|luxuryestate|seloger")


def norm_site(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    if not host or NOT_OWN.search(host):
        return None
    return f"https://{host}/"


def mcre_agency_info(f: PoliteFetcher, slug: str) -> dict:
    h = f.get(MCRE + slug)
    out = {}
    m = re.search(r'Website:.*?href="([^"]+)"', h, re.S)
    if m:
        out["website"] = norm_site(m.group(1))
    m = re.search(r'Phone number:.*?<span[^>]*>([^<]+)</span>', h, re.S)
    if m:
        out["phone"] = m.group(1).strip()
    m = re.search(r'detailAgenziaCard__address">([^<]+)<', h)
    if m:
        out["address"] = m.group(1).strip()
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    agencies = json.loads((DATA / "agencies.json").read_text())
    f = PoliteFetcher(delay=(2, 4), block_wait=15)

    for a in agencies:
        a["website"] = norm_site(a.get("website"))
        if a.get("mcre_slug") and not a.get("website"):
            try:
                info = mcre_agency_info(f, a["mcre_slug"])
                a["website"] = info.get("website")
                a.setdefault("phone", info.get("phone"))
                a.setdefault("address", info.get("address"))
            except Exception as e:
                log.warning("%s: %s", a["mcre_slug"], e)

    # Map the original CSV (255 names) onto real agencies.
    recon = {r["name"]: r for r in json.loads((DATA / "site_recon.json").read_text())}
    mapping = []
    for row in csv.DictReader(open(DATA / "monaco_agencies.csv")):
        best, score = None, 0
        for a in agencies:
            sc = match_score(a["name"], row["Name"])
            if sc > score:
                best, score = a, sc
        live = bool(recon.get(row["Name"], {}).get("reachable"))
        site = norm_site(row["Website"]) if live else None
        if best is not None:
            best.setdefault("csv_names", []).append(row["Name"])
            if site and not best.get("website"):
                best["website"] = site
        elif site:
            # Real, reachable site for a name on neither portal nor directory:
            # keep it as its own agency (portals like rightmove filtered by NOT_OWN).
            agencies.append({"name": row["Name"], "cim_member": False, "website": site, "csv_names": [row["Name"]],
                             "covered_by": []})
            best = agencies[-1]
        mapping.append({"csv_name": row["Name"], "csv_website": row["Website"], "csv_domain_live": live,
                        "agency": best["name"] if best else None, "website": best.get("website") if best else None})

    (DATA / "agencies.json").write_text(json.dumps(agencies, ensure_ascii=False, indent=1))
    (DATA / "csv_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1))
    print(f"{len(agencies)} agencies, {sum(1 for a in agencies if a.get('website'))} with website; "
          f"CSV: {sum(1 for m in mapping if m['agency'])}/{len(mapping)} names mapped to a real agency")


if __name__ == "__main__":
    main()
