"""
CIM portal scraper — chambre-immobiliere-monaco.mc
===================================================
The Chambre Immobilière Monégasque portal carries the listings of its ~95
member agencies as plain server-rendered HTML: price, charges, type, rooms,
bedrooms, bathrooms, parking, area, terrace, floor, quarter + building
(breadcrumbs), photos, exclusivity and the agency's phone/email/website.
No individual agent — per-agency scrapers add that later and dedup merges.

Each member agency is its own site run (site_key "cim-<slug>") so listings
are attributed to the right agency and one agency's failure never affects
another's removal bookkeeping.

Usage:
    python -m scraper.cim agencies                      # -> data/cim_agencies.json
    python -m scraper.cim run [--slug S ...] [--limit N] [--dry-run]
    python -m scraper.cim detail <url>                  # print one parsed listing

Env for `run` without --dry-run: SYNC_API_BASE_URL, SYNC_API_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.fetch import Blocked, NotFound, PoliteFetcher  # noqa: E402
from scraper.runner import run_site  # noqa: E402
from scraper.text_rules import keyword_flags, parse_number  # noqa: E402

log = logging.getLogger("cim")

BASE = "https://www.chambre-immobiliere-monaco.mc"
DATA = Path(__file__).resolve().parents[1] / "data"
# The map is centred here when a listing has no coordinates of its own.
DEFAULT_CENTER = (43.74, 7.42)

TYPE_MAP = {
    "appartement": "apartment", "studio": "apartment", "duplex": "duplex", "triplex": "duplex",
    "penthouse": "penthouse", "roof": "penthouse", "villa": "villa", "maison": "villa",
    "bureau": "office", "bureaux": "office", "local commercial": "shop", "commerce": "shop",
    "boutique": "shop", "entrepot": "warehouse", "entrepôt": "warehouse", "dépôt": "warehouse",
    "parking": "parking", "box": "parking", "cave": "cellar", "fonds de commerce": "business",
    "droit au bail": "business", "hôtel particulier": "villa", "terrain": "land",
    "garage": "parking", "loft": "apartment", "rez-de-jardin": "apartment", "chambre de service": "maid_room",
    "local": "shop", "murs local commercial": "shop", "cessions de droit au bail": "business",
    "penthouse/roof": "penthouse", "roof top": "penthouse", "rooftop": "penthouse",
}


def canonical_url(href: str) -> str:
    """/fr/bien/105641/any-slug -> https://…/fr/bien/105641/bien. The portal
    needs a slug but ignores its value; a fixed one keeps the URL (our
    dedup key) stable when the agency edits the title."""
    m = re.search(r"/fr/bien/(\d+)", href)
    return f"{BASE}/fr/bien/{m.group(1)}/bien" if m else href


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ── agencies ────────────────────────────────────────────────────────────

def parse_agencies(html: str) -> list[dict]:
    """The members page nests unclosed <article>s, so walk names and links
    in document order: a link belongs to the name before it, and an agency
    without a "Voir tous les biens" link simply has no cim_slug."""
    out: list[dict] = []
    seen: set[str] = set()
    for el in soup(html).select('.nomagence, .responsable, address, a[href*="/fr/agence/"]'):
        if "nomagence" in (el.get("class") or []):
            out.append({"name": el.get_text(" ", strip=True), "cim_slug": None, "manager": None, "address": None})
        elif not out:
            continue
        elif el.name == "a":
            m = re.search(r"/fr/agence/([^/]+)/grid", el["href"])
            if m and out[-1]["cim_slug"] is None and m.group(1) not in seen:
                out[-1]["cim_slug"] = m.group(1)
                seen.add(m.group(1))
        elif el.name == "address":
            out[-1]["address"] = out[-1]["address"] or el.get_text(" ", strip=True)
        else:
            out[-1]["manager"] = out[-1]["manager"] or el.get_text(" ", strip=True)
    return out


def parse_agency_contact(html: str) -> dict:
    """Agency block on a listing detail page."""
    b = soup(html)
    tel = b.select_one("a.clickTel")
    mail = b.select_one("a.clickMail")
    url = b.select_one("a.clickURL")
    name = b.select_one("p.nomagence")
    return {
        "name": name.get_text(" ", strip=True) if name else None,
        "phone": tel["href"].removeprefix("tel:") if tel else None,
        "email": mail.get_text(strip=True) if mail else None,
        "website": url["href"] if url else None,
    }


# ── grid (index) ────────────────────────────────────────────────────────

def parse_grid(html: str) -> tuple[list[dict], int]:
    """Returns (cards, last_page). Card: source_url, price, title."""
    b = soup(html)
    cards = []
    for art in b.select("article[data-id]"):
        a = art.find("a", href=re.compile(r"/fr/bien/\d+"))
        if not a:
            continue
        h3 = art.find("h3")
        prix = art.select_one(".prix")
        price = parse_number(prix.get_text(" ", strip=True)) if prix else None
        cards.append({
            "source_url": canonical_url(a["href"]),
            "price": price,
            "title": h3.get_text(" ", strip=True) if h3 else None,
        })
    pages = [int(li["data-counter"]) for li in b.select("ul.pagine li[data-counter]") if li["data-counter"].isdigit()]
    return cards, max(pages or [1])


def crawl_index(f: PoliteFetcher, slug: str) -> list[dict]:
    """All cards of one agency. Raises Blocked if any page fails, so an
    incomplete index is never reported as complete."""
    cards, last = parse_grid(f.get(f"{BASE}/fr/agence/{slug}/grid"))
    for page in range(2, last + 1):
        more, _ = parse_grid(f.get(f"{BASE}/fr/agence/{slug}/grid/{page}"))
        cards += more
    seen, out = set(), []
    for c in cards:
        if c["source_url"] not in seen:
            seen.add(c["source_url"])
            out.append(c)
    return out


# ── detail ──────────────────────────────────────────────────────────────

def parse_detail(html: str, url: str) -> dict:
    b = soup(html)
    carac = {}
    box = b.select_one("div.caracs")
    if box is None:
        raise NotFound(f"{url}: no listing on page")
    if box:
        for p in box.select("p"):
            if p.span and p.contents and isinstance(p.contents[0], str):
                carac[p.contents[0].strip().lower()] = p.span.get_text(" ", strip=True)

    tags = [p.get_text(strip=True).lower() for p in b.select(".zoomlibri .tag p")]
    crumbs = [(a.get_text(" ", strip=True), a.get("href", "")) for a in b.select("nav.breadcrumbs a")]
    transaction = "rent" if any("/locations" in h for _, h in crumbs) or "location" in tags else "sale"
    quarter = next((t for t, h in crumbs if "/q_" in h), None)
    building = next((t for t, h in crumbs if "/immeuble/" in h), None)

    h1 = b.find("h1")
    if h1:
        for p in h1.find_all("p"):
            p.extract()
    title = h1.get_text(" ", strip=True) if h1 else None

    desc = None
    h2 = next((h for h in b.find_all("h2") if h.get_text(strip=True).lower() == "description"), None)
    if h2:
        parts = [s.get_text("\n", strip=True) for s in h2.find_next_siblings()]
        desc = "\n".join(p for p in parts if p) or None

    price_label = next((k for k in carac if k.startswith("prix") or k.startswith("montant du loyer") or k == "loyer"), None)
    price_text = carac.get(price_label, "") if price_label else ""
    price = parse_number(price_text)
    rent_period = None
    if transaction == "rent":
        low = price_text.lower()
        rent_period = "week" if "semaine" in low else "year" if "an" in low.split("/")[-1] else "season" if "saison" in low else "month"

    raw_type = carac.get("type de bien")
    ptype = TYPE_MAP.get((raw_type or "").strip().lower()) or ((raw_type or "").strip().lower() or None)

    lat = lng = None
    # The template keeps a commented-out "// center:[43.74,7.42]" line.
    for m in re.finditer(r"^(?!\s*//).*?center:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", html, re.M):
        la, ln = float(m.group(1)), float(m.group(2))
        if (round(la, 4), round(ln, 4)) != DEFAULT_CENTER:
            lat, lng = la, ln
            break

    photos = []
    for a in b.select("a.photogallery[href]"):
        mm = re.search(r"(/medias_upload/biens/[^\"'?#]+)", a["href"])
        if mm and BASE + mm.group(1) not in photos:
            photos.append(BASE + mm.group(1))

    ref = b.select_one("p.ref")
    agency = parse_agency_contact(html)
    flags = keyword_flags(f"{title or ''}\n{desc or ''}")
    parking = parse_number(carac.get("parking(s)", ""))
    charges = parse_number(carac.get("montant des charges", ""))

    # "Superf. totale" includes the terrace (checked against the same
    # listing on MCRE: 152 total = 124 living + 28 terrace). Store living
    # area like every other source so dedup compares like with like.
    total = parse_number(carac.get("superf. totale", ""))
    terrace = parse_number(carac.get("superf. terrasse", ""))
    living = round(total - terrace, 2) if total and terrace and total > terrace else total
    if isinstance(living, float) and living.is_integer():
        living = int(living)

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
        "rooms": _int(carac.get("nb de pièces")),
        "bedrooms": _int(carac.get("chambre(s)")),
        "bathrooms": _int(carac.get("salle(s) de bain")),
        "living_area_sqm": living,
        "terrace_sqm": terrace,
        "floor": _floor(carac.get("étage")),
        "parking": int(parking) if parking is not None else (1 if flags.get("parking") else None),
        "cellar": True if ptype == "cellar" or flags.get("cellar") else None,
        "sea_view": True if flags.get("sea_view") else None,
        "building_name": building,
        "quarter": quarter,
        "lat": lat,
        "lng": lng,
        "external_ref": ref.get_text(" ", strip=True).removeprefix("réf").strip() if ref else None,
        "agency_phone": agency["phone"],
        "agency_email": agency["email"],
        "photo_urls": photos,
        "extra": {k: v for k, v in {
            "exclusive": "exclusivité" in tags or None,
            "charges": charges,
            "total_area_sqm": total if total != living else None,
            "property_type_raw": raw_type,
            "cim_agency": agency["name"],
            "agency_website": agency["website"],
        }.items() if v is not None},
    }
    # Studio = 1 room, 0 bedrooms when the site leaves bedrooms blank.
    if listing["bedrooms"] is None and listing["rooms"] == 1:
        listing["bedrooms"] = 0
    return listing


def _int(v: str | None) -> int | None:
    n = parse_number(v or "")
    return int(n) if n is not None else None


def _floor(v: str | None) -> int | None:
    if not v:
        return None
    low = v.lower()
    if low.startswith(("rdc", "rez")):
        return 0
    return _int(v)


# ── run ─────────────────────────────────────────────────────────────────

def run_agency(f: PoliteFetcher, agency: dict, client=None, limit: int | None = None, dry_run: bool = False) -> dict:
    """One site run for one member agency. Never raises: returns a status dict."""
    slug = agency["cim_slug"]
    site_key = f"cim-{slug}"[:60]
    if dry_run:
        cards = crawl_index(f, slug)
        out = []
        for c in cards[: limit or 3]:
            out.append(parse_detail(f.get(c["source_url"]), c["source_url"]))
        print(json.dumps({"site_key": site_key, "index_count": len(cards), "sample": out}, ensure_ascii=False, indent=1))
        return {"site_key": site_key, "status": "dry-run", "index_count": len(cards)}

    info = {"name": agency["name"], "cim_slug": slug, "manager": agency.get("manager"), "address": agency.get("address"),
            **{k: agency[k] for k in ("website", "phone", "email") if agency.get(k)}}
    return run_site(f, client, site_key, info, lambda: crawl_index(f, slug), parse_detail, limit=limit)


def load_agencies() -> list[dict]:
    return json.loads((DATA / "cim_agencies.json").read_text())


def cmd_agencies(f: PoliteFetcher) -> None:
    """Member list + each agency's contacts (from its first listing) and listing count."""
    agencies = parse_agencies(f.get(f"{BASE}/fr/agences"))
    for a in agencies:
        try:
            cards = crawl_index(f, a["cim_slug"])
            a["cim_listing_count"] = len(cards)
            if cards:
                c = parse_agency_contact(f.get(cards[0]["source_url"]))
                a.update({k: v for k, v in c.items() if k != "name" and v})
        except (Blocked, NotFound) as e:
            a["error"] = str(e)
        log.info("%s: %s listings", a["cim_slug"], a.get("cim_listing_count"))
    (DATA / "cim_agencies.json").write_text(json.dumps(agencies, ensure_ascii=False, indent=1))
    print(f"{len(agencies)} agencies, {sum(a.get('cim_listing_count', 0) for a in agencies)} listings")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("agencies")
    r = sub.add_parser("run")
    r.add_argument("--slug", action="append")
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
        agencies = [a for a in load_agencies() if not args.slug or a["cim_slug"] in args.slug]
        client = None
        if not args.dry_run:
            from sync.worker_client import WorkerClient
            client = WorkerClient()
        for a in agencies:
            if not a.get("cim_listing_count") and not args.slug:
                continue
            try:
                res = run_agency(f, a, client, args.limit, args.dry_run)
            except Exception:  # dry-run path; run_site itself never raises
                log.exception("agency %s failed", a["cim_slug"])
                continue
            log.info("done %s", res)


if __name__ == "__main__":
    main()
