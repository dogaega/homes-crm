"""
Monte-Carlo Real Estate portal scraper — montecarlo-realestate.com
===================================================================
Second backbone next to the CIM portal: ~118 Monaco agencies (members and
non-members), server-rendered HTML with a full features table (type,
contract, price, floor, rooms, bedrooms, bathrooms, cellars, parking,
living/total/terrace area, district, building, feature tags), all photos
and the agency's phone. No coordinates and no individual agent.

One site run per agency (site_key "mcre-<tc id>"), index =
/en/estate-agents/<slug>/properties (paginated via rel=next), Monaco only.

Usage:
    python -m scraper.mcre agencies                 # -> data/mcre_agencies.json
    python -m scraper.mcre run [--tc N ...] [--limit N] [--dry-run]
    python -m scraper.mcre detail <url>
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import logging
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.fetch import Blocked, NotFound, PoliteFetcher  # noqa: E402
from scraper.runner import run_site  # noqa: E402
from scraper.text_rules import keyword_flags, parse_number, title_status  # noqa: E402

log = logging.getLogger("mcre")

BASE = "https://www.montecarlo-realestate.com"
IMG = "https://img.montecarlo-realestate.com/annunci/XXL/{}/foto.jpg"
DATA = Path(__file__).resolve().parents[1] / "data"

TYPE_MAP = {
    "apartment": "apartment", "studio": "apartment", "loft": "apartment", "duplex": "duplex",
    "triplex": "duplex", "penthouse": "penthouse", "attic": "penthouse", "villa": "villa",
    "detached house": "villa", "house": "villa", "townhouse": "villa", "office": "office",
    "shop": "shop", "commercial": "shop", "retail": "shop", "warehouse": "warehouse",
    "storage": "warehouse", "parking space": "parking", "garage": "parking", "box": "parking",
    "cellar": "cellar", "business": "business", "land": "land", "building": "building",
    "plot": "land", "maid room": "maid_room", "maid's room": "maid_room", "service room": "maid_room",
    "parking": "parking", "commercial premises": "shop", "business premises": "shop",
}


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def canonical_url(href: str) -> str:
    m = re.search(r"/properties/(mc-tc-\d+-\d+)", href)
    return f"{BASE}/en/properties/{m.group(1)}" if m else href


# ── agencies ────────────────────────────────────────────────────────────

def parse_agency_list(html: str) -> tuple[dict[str, dict], str | None]:
    b = soup(html)
    out: dict[str, dict] = {}
    for a in b.select('a[href*="-monaco-tc-"]'):
        m = re.search(r"/en/estate-agents/([a-z0-9-]+-monaco-tc-(\d+))", a["href"])
        if not m:
            continue
        e = out.setdefault(m.group(1), {"mcre_slug": m.group(1), "mcre_tc": int(m.group(2)), "name": None})
        t = a.get_text(" ", strip=True)
        if t and not e["name"] and not re.match(r"^\d|see |view |properties", t, re.I):
            e["name"] = t
    nx = b.select_one("link[rel=next]")
    return out, nx["href"] if nx else None


# ── index ───────────────────────────────────────────────────────────────

def parse_index(html: str) -> tuple[list[dict], str | None]:
    b = soup(html)
    cards = []
    for a in b.select("a.card__title[href*='/properties/mc-tc-']"):
        card = a.find_parent(class_="card") or a.parent
        q = card.select_one(".card__quartiere")
        if q and not q.get_text(strip=True).lower().startswith("monaco"):
            continue  # agency's French/Italian listings: out of scope
        price = card.select_one(".card__price")
        cards.append({
            "source_url": canonical_url(a["href"]),
            "price": parse_number(price.get_text(" ", strip=True)) if price else None,
            "title": a.get_text(" ", strip=True),
        })
    nx = b.select_one("link[rel=next]")
    return cards, nx["href"] if nx else None


def crawl_index(f: PoliteFetcher, slug: str) -> list[dict]:
    url: str | None = f"{BASE}/en/estate-agents/{slug}/properties"
    cards: list[dict] = []
    pages = 0
    while url and pages < 60:
        more, url = parse_index(f.get(url))
        cards += more
        pages += 1
    seen, out = set(), []
    for c in cards:
        if c["source_url"] not in seen:
            seen.add(c["source_url"])
            out.append(c)
    return out


# ── detail ──────────────────────────────────────────────────────────────

def parse_detail(html: str, url: str) -> dict | None:
    b = soup(html)
    table: dict[str, str] = {}
    for dl in b.select(".immobileDetails__table dl.row"):
        dt, dd = dl.select_one("dt.term"), dl.select_one("dd.description")
        if dt and dd:
            table[dt.get_text(strip=True).rstrip(":").lower()] = dd.get_text(" ", strip=True)
    if not table:
        raise NotFound(f"{url}: no listing on page")
    tags = [t.get_text(" ", strip=True).lower() for t in b.select(".immobileDetails__tagLabel")]

    h1 = b.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else None
    d = b.select_one(".immobileDetails__text")
    desc = d.get_text("\n", strip=True) if d else None

    contract = (table.get("contract") or "").lower()
    transaction = "rent" if "rent" in contract or "let" in contract else "sale"
    price_text = table.get("price", "")
    price = parse_number(price_text)
    rent_period = None
    if transaction == "rent":
        low = price_text.lower()
        rent_period = "week" if "week" in low else "year" if "year" in low else "season" if "season" in low else "month"

    raw_type = table.get("type of property")
    ptype = TYPE_MAP.get((raw_type or "").strip().lower()) or ((raw_type or "").strip().lower() or None)

    living = parse_number(table.get("living area", ""))
    total = parse_number(table.get("total area", ""))

    # Only this listing's photos (the page also shows "similar properties").
    lid = re.search(r"mc-tc-(\d+)-(\d+)", url)
    own = f"A_{lid.group(1)}_{lid.group(2)}_" if lid else "A_"
    photo_ids = [p for p in dict.fromkeys(re.findall(r"/annunci/[^/]+/(A_\d+_\d+_\d+)/", html)) if p.startswith(own)]
    phone = next((htmllib.unescape(a["href"])[4:] for a in b.select('a[href^="tel:"]')), None)
    ag = b.select_one(".sideBox__agenzia a.agenzia__name")
    ag_addr = b.select_one(".sideBox__agenzia .agenzia__address")

    flags = keyword_flags(f"{title or ''}\n{desc or ''}\n{' '.join(tags)}")
    cellars = parse_number(table.get("cellars", ""))
    parking = parse_number(table.get("parking spaces", ""))
    listing = {
        "source_url": canonical_url(url),
        "detail": True,
        "transaction_type": transaction,
        "price": price,
        "price_on_request": price is None,
        "currency": "EUR",
        "rent_period": rent_period,
        "title": title,
        "description": desc,
        "property_type": ptype,
        "rooms": _int(table.get("rooms")),
        "bedrooms": _int(table.get("bedrooms")),
        "bathrooms": _int(table.get("bathrooms")),
        "living_area_sqm": living or total,
        "terrace_sqm": parse_number(table.get("terraced area", "")),
        "floor": _floor(table.get("floor")),
        "parking": int(parking) if parking is not None else (1 if "parking space" in tags else None),
        "cellar": True if (cellars or ptype == "cellar" or "cellar" in tags) else None,
        "sea_view": True if ("sea view" in tags or flags.get("sea_view")) else None,
        "building_name": table.get("building"),
        "quarter": table.get("district"),
        "external_ref": table.get("reference"),
        "agency_phone": phone,
        "photo_urls": [IMG.format(p) for p in photo_ids],
        "extra": {k: v for k, v in {
            "exclusive": "exclusive" in tags or None,
            "new_build": "new construction" in tags or None,
            "panoramic_view": "with panoramic view" in tags or None,
            "total_area_sqm": total if living and total and total != living else None,
            "tags": tags or None,
            "property_type_raw": raw_type,
            "mcre_agency": ag.get_text(" ", strip=True) if ag else None,
            "mcre_agency_address": ag_addr.get_text(" ", strip=True) if ag_addr else None,
        }.items() if v is not None},
    }
    if listing["bedrooms"] is None and listing["rooms"] == 1:
        listing["bedrooms"] = 0
    status = title_status(title)
    if status == "gone":
        return None  # already sold/rented: the runner skips it
    if status == "under_offer":
        listing["extra"]["under_offer"] = True
    return listing


def _int(v: str | None) -> int | None:
    n = parse_number(v or "")
    return int(n) if n is not None else None


def _floor(v: str | None) -> int | None:
    if not v:
        return None
    low = v.lower()
    if low.startswith(("ground", "rdc", "rez", "garden")):
        return 0
    if "mezzanine" in low:
        return 0
    if low.startswith(("basement", "semi-basement")):
        return -1
    return _int(v)


# ── commands ────────────────────────────────────────────────────────────

def run_agency(f: PoliteFetcher, agency: dict, client=None, limit: int | None = None, dry_run: bool = False) -> dict:
    slug, tc = agency["mcre_slug"], agency["mcre_tc"]
    site_key = f"mcre-{tc}"
    if dry_run:
        cards = crawl_index(f, slug)
        out = [parse_detail(f.get(c["source_url"]), c["source_url"]) for c in cards[: limit or 3]]
        print(json.dumps({"site_key": site_key, "index_count": len(cards), "sample": out}, ensure_ascii=False, indent=1))
        return {"site_key": site_key, "status": "dry-run", "index_count": len(cards)}
    info = {k: v for k, v in {
        "name": agency.get("name"), "cim_slug": agency.get("cim_slug"), "website": agency.get("website"),
        "phone": agency.get("phone"), "address": agency.get("address"),
    }.items() if v}
    return run_site(f, client, site_key, info, lambda: crawl_index(f, slug), parse_detail, limit=limit)


def cmd_agencies(f: PoliteFetcher) -> None:
    url: str | None = f"{BASE}/en/estate-agents/monaco"
    agencies: dict[str, dict] = {}
    while url:
        found, url = parse_agency_list(f.get(url))
        for k, v in found.items():
            agencies.setdefault(k, v)
            agencies[k]["name"] = agencies[k]["name"] or v["name"]
    out = []
    for a in agencies.values():
        try:
            a["mcre_listing_count"] = len(crawl_index(f, a["mcre_slug"]))
        except (Blocked, NotFound) as e:
            a["error"] = str(e)
        log.info("%s: %s listings", a["mcre_slug"], a.get("mcre_listing_count"))
        out.append(a)
    (DATA / "mcre_agencies.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{len(out)} agencies, {sum(a.get('mcre_listing_count', 0) for a in out)} Monaco listings")


def load_agencies() -> list[dict]:
    """data/agencies.json (merged master list) entries that have an MCRE page."""
    p = DATA / "agencies.json"
    src = json.loads(p.read_text()) if p.exists() else json.loads((DATA / "mcre_agencies.json").read_text())
    return [a for a in src if a.get("mcre_slug")]


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("agencies")
    r = sub.add_parser("run")
    r.add_argument("--tc", type=int, action="append")
    r.add_argument("--limit", type=int)
    r.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("detail")
    d.add_argument("url")
    ap.add_argument("--fast", action="store_true", help="1–2 s delays (testing only)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    f = PoliteFetcher(delay=(1, 2) if args.fast else (3, 8))

    if args.cmd == "agencies":
        cmd_agencies(f)
    elif args.cmd == "detail":
        print(json.dumps(parse_detail(f.get(args.url), args.url), ensure_ascii=False, indent=1))
    else:
        client = None
        if not args.dry_run:
            from sync.worker_client import WorkerClient
            client = WorkerClient()
        for a in load_agencies():
            if args.tc and a["mcre_tc"] not in args.tc:
                continue
            if not args.tc and not a.get("mcre_listing_count"):
                continue
            try:
                res = run_agency(f, a, client, args.limit, args.dry_run)
            except Exception:  # dry-run path; run_site itself never raises
                log.exception("agency %s failed", a["mcre_slug"])
                continue
            log.info("done %s", res)


if __name__ == "__main__":
    main()
