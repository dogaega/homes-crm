"""
Notaries' listings (immobilier.notaires.fr) for the Alpes-Maritimes and the
Var, from the JSON feed behind the site's own search pages. One request per
100 listings, 10 s apart (the site's robots.txt crawl delay); the feed already
holds everything a detail page would, so detail pages are never fetched.
"""

from __future__ import annotations

import json
import re

from scraper.fetch import PoliteFetcher

API = "https://www.immobilier.notaires.fr/pub-services/inotr-www-annonces/v1/annonces"
DEPTS = ("06", "83")
TYPES = {"APP": "apartment", "MAI": "house", "TER": "land", "GAR": "parking", "IMM": "building", "LAC": "commercial"}
LABELS = {"APP": "Appartement", "MAI": "Maison", "TER": "Terrain", "GAR": "Parking", "IMM": "Immeuble", "LAC": "Local"}
SQM = re.compile(r"(\d{2,4}(?:[.,]\d{1,2})?)\s*m(?:²|2)\b", re.I)
TRANSACTIONS = {"VENTE": "sale", "LOCATION": "rent"}  # VNI / VAE are auctions: no fixed price


class FeedFetcher:
    """run_site fetches each listing URL: hand it the feed record instead of the page."""

    def __init__(self, f: PoliteFetcher):
        self.f, self.records = f, {}

    def get(self, url: str) -> str:
        return self.records[url] if url in self.records else self.f.get(url)

    def __getattr__(self, name):
        return getattr(self.f, name)


def crawl_index(ff: FeedFetcher) -> list[dict]:
    cards = []
    for dept in DEPTS:
        page, pages = 1, 1
        while page <= pages:
            d = json.loads(ff.f.get(f"{API}?departements={dept}&parPage=100&page={page}"))
            pages = d.get("nbPages") or 1
            for a in d.get("annonceResumeDto") or []:
                url = a.get("urlDetailAnnonceFr")
                if not url or a.get("statut") != "LIGNE" or a.get("typeTransaction") not in TRANSACTIONS or a.get("viager") == "OUI":
                    continue
                ff.records[url] = json.dumps(a)
                cards.append({"source_url": url, "transaction_hint": TRANSACTIONS[a["typeTransaction"]]})
            page += 1
    return cards


def parse_detail(text: str, url: str) -> dict | None:
    a = json.loads(text)
    tx = TRANSACTIONS[a["typeTransaction"]]
    town = (a.get("localiteNom") or a.get("communeNom") or "").title()
    kind = TYPES.get(a.get("typeBien"), "property")
    rooms = a.get("nbPieces")
    title = f"{LABELS.get(a.get('typeBien'), 'Bien')} {f'{rooms} pièces ' if rooms else ''}à {town}" + (f" – {a['quartierNom']}" if a.get("quartierNom") else "")
    # Land: "surface" is the plot. Elsewhere it is sometimes missing: first "N m²" of the description.
    living = None if kind == "land" else a.get("surface")
    if living is None and kind != "land":
        m = SQM.search(a.get("descriptionFr") or "")
        living = float(m.group(1).replace(",", ".")) if m else None
    return {
        "source_url": url,
        "detail": True,
        "transaction_type": tx,
        # Buyers pay the displayed price plus the notary's fee (prixTotal): the advertised figure is prixAffiche.
        "price": a.get("prixAffiche") or a.get("prixTotal"),
        "price_on_request": not (a.get("prixAffiche") or a.get("prixTotal")),
        "currency": "EUR",
        "rent_period": "month" if tx == "rent" else None,
        "title": title,
        "description": a.get("descriptionFr"),
        "property_type": kind,
        "rooms": rooms,
        "bedrooms": a.get("nbChambres"),
        "living_area_sqm": living,
        "quarter": a.get("quartierNom"),
        "address": f"{a.get('codePostal') or ''} {town}".strip(),
        "external_ref": a.get("reference"),
        "photo_urls": [a["urlPhotoPrincipale"]] if a.get("urlPhotoPrincipale") else [],
        "agency_phone": a.get("telephone"),
        "extra": {k: v for k, v in {"plot_sqm": a.get("surfaceTerrain") or (a.get("surface") if kind == "land" else None), "notary_fee": a.get("emoluments"),
                                    "price_with_fee": a.get("prixTotal"), "notary_office": a.get("crpcen")}.items() if v},
    }
