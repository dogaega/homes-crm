"""
Generic agency-website scraper, driven by a per-site config
(data/site_configs.json, drafted from recon/recon_websites.py and verified
by sampling). Extraction without LLM, in order of trust:
  1. JSON-LD (schema.org Offer / Residence / Product …)
  2. label → value pairs (dt/dd, th/td, "Label: value", label/value spans)
     in FR / EN / IT / RU
  3. OpenGraph + page text fallbacks (price, m², agent block)

Config per site:
  {"site_key": "web-<host>", "agency": "<name in agencies.json>",
   "index_urls": {"sale": [...], "rent": [...]},
   "listing_pattern": "<regex on absolute URL>",
   "runner": "server" | "local", "max_pages": 60,
   "overrides": {"<field>": "<css selector>"},     # optional bespoke fixes ("photos": gallery container)
   "remove": ["<css selector>", ...],              # optional: blocks that are not the listing
   "transaction": "sale" | "rent",                 # optional: single-type sites
   "accept_404": true,                             # optional: site serves real pages with status 404
   "skip_if": "<css selector>"}                    # optional: page is not wanted when it matches (holiday lets)
"""

from __future__ import annotations

import html as htmllib
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraper.fetch import NotFound, PoliteFetcher
from scraper.text_rules import keyword_flags, parse_number

# ── label dictionary ────────────────────────────────────────────────────
LABELS: list[tuple[str, re.Pattern]] = [(k, re.compile(v, re.I)) for k, v in [
    ("rent_price", r"^(?:loyer|montant du loyer|rent|monthly rent|loyer mensuel|affitto|аренда)\b"),
    ("price", r"^(?:prix|price|prix de vente|sale price|asking price|prezzo|цена|tarif)\b"),
    ("charges", r"^(?:charges|service charges?|spese)\b"),
    ("bedrooms", r"^(?:nb\.? de )?(?:chambres?|bedrooms?|beds?|camere(?: da letto)?|спальн)"),
    ("rooms", r"^(?:nb\.? de )?(?:pi[eè]ces?|rooms?|nombre de pi[eè]ces|locali|vani|комнат)"),
    ("bathrooms", r"^(?:nb\.? de )?(?:salles? de bains?|salles? d.eau|bathrooms?|baths?|bagni|ванн)"),
    ("terrace", r"^(?:superf(?:icie|\.)? )?(?:terrasses?|terraces?|balcon|balcony|loggia|terrazz)"),
    ("living_hab", r"^(?:surface habitable|superf(?:icie|\.)? ?hab|surf\.? hab|living area|living space|surface utile|"
                   r"interior|internal area|superficie interna|жилая)"),
    ("living", r"^(?:surface|superficie|superf\.|living area|living space|area|size|surface habitable|"
               r"surface totale|total area|interior|superficie interna|площадь|m²|sqm|sq\.? ?m)"),
    ("floor", r"^(?:[eé]tage|floor|level|niveau|piano|этаж)\b"),
    ("parking", r"^(?:parkings?|garages?|parking spaces?|box|posti auto|парковк)"),
    ("cellar", r"^(?:caves?|cellars?|cantina|storage)\b"),
    ("reference", r"^(?:r[éeè]f(?:[ée]rence)?\.?|reference|ref\.?|mandat|riferimento|id)\b"),
    ("transaction", r"^(?:transaction|contract|type de transaction|type of transaction|offre|contrat)\b"),
    ("type", r"^(?:type(?! de transaction| of transaction)(?: de (?:bien|produit))?|property type|typology|tipologia|"
             r"type of property|тип)"),
    ("quarter", r"^(?:quartier|district|neighbou?rhood|area|secteur|zone|quartiere|район|location|localisation)\b"),
    ("building", r"^(?:immeuble|r[ée]sidence|building|residence|palazzo|здание)\b"),

]]

AGENT_SECTION = re.compile(r"(votre\s+(?:contact|conseill[eè]re?|agent|interlocut)|your\s+(?:contact|agent|advisor|"
                           r"consultant)|agent\s+in\s+charge|n[ée]gociat|contact\s+person|conseiller|consultant|"
                           r"property\s+advisor|responsable|agent\b|advisor|interlocuteur)", re.I)
PERSON = re.compile(r"\b([A-ZÀ-Ý][a-zà-ÿ'’-]+(?:\s+(?:de|di|da|van|von|le|la|del)\b)?(?:\s+[A-ZÀ-Ý][A-Za-zà-ÿ'’-]+){1,2})\b")
NOT_PERSON = re.compile(r"monaco|monte|carlo|real|estate|immobili|immeuble|r[ée]sidence|palace|palais|villa|"
                        r"tower|park|agence|agency|contact|propert|prix|price|"
                        r"voir|view|send|envoyer|appeler|call|visite|visit|boulevard|avenue|rue|place|chambre|"
                        r"bedroom|salle|group|sam\b|sarl|luxury|prestige|international|properties|homes|"
                        r"privacy|cookie|mentions|conditions|terms|galerie|gallery|r[ée]sum[ée]|r[ée]f[ée]rence|"
                        r"description|d[ée]tails?|surface|jardin|exotique|condamine|fontvieille|larvotto|moneghetti|"
                        r"revoires|r[ée]voires|rousse|saint|portier|mareterra|carr[ée]|golden|square", re.I)
ROLE_WORDS = re.compile(r"\s+(?:Agent|Agente|Director|Directrice|Directeur|Manager|Consultant|Consultante|Associate|"
                        r"Partner|Associ[ée]e?|N[ée]gociat(?:eur|rice)|Conseill[eè]re?|Founder|Fondat(?:eur|rice)|CEO|"
                        r"Sales|Senior|Junior|Broker|Advisor|Assistant|Assistante|Gérant|Gérante)\b.*$")
IMG_EXT = re.compile(r"\.(?:jpe?g|png|webp)(?:\?|$)", re.I)
IMG_JUNK = re.compile(r"logo|icon|sprite|avatar|placeholder|flag|marker|pin\b|loader|blank|pixel|"
                      r"favicon|badge|banner|thumb[_-]?\d{2}\b|/wp-content/themes/|/_?templates?[a-z]?/", re.I)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def clean(t: str | None) -> str | None:
    if t is None:
        return None
    t = re.sub(r"\s+", " ", htmllib.unescape(t)).strip(" : -–|")
    return t or None


# ── JSON-LD ─────────────────────────────────────────────────────────────

def jsonld(b: BeautifulSoup) -> dict:
    out: dict = {}

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
            return
        if not isinstance(o, dict):
            return
        if "@graph" in o:
            walk(o["@graph"])
        t = o.get("@type")
        t = " ".join(t) if isinstance(t, list) else str(t or "")
        offers = o.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict) and offers.get("price") is not None:
            out.setdefault("price", parse_number(str(offers.get("price"))))
            out.setdefault("currency", offers.get("priceCurrency"))
            seller = offers.get("seller") or offers.get("offeredBy")
            if isinstance(seller, dict) and seller.get("@type") == "Person":
                out.setdefault("agent_name", seller.get("name"))
                out.setdefault("agent_phone", seller.get("telephone"))
                out.setdefault("agent_email", seller.get("email"))
        if any(k in t for k in ("Residence", "Apartment", "House", "Accommodation", "SingleFamily", "Product",
                                "RealEstateListing", "Offer", "Place")):
            out.setdefault("title", o.get("name"))
            out.setdefault("description", o.get("description"))
            fs = o.get("floorSize")
            if isinstance(fs, dict):
                out.setdefault("living", parse_number(str(fs.get("value"))))
            for k_src, k_dst in (("numberOfRooms", "rooms"), ("numberOfBedrooms", "bedrooms"),
                                 ("numberOfBathroomsTotal", "bathrooms"), ("floorLevel", "floor")):
                if o.get(k_src) is not None:
                    v = o[k_src]
                    out.setdefault(k_dst, parse_number(str(v.get("value") if isinstance(v, dict) else v)))
            geo = o.get("geo")
            if isinstance(geo, dict) and geo.get("latitude"):
                out.setdefault("lat", parse_number(str(geo.get("latitude"))))
                out.setdefault("lng", parse_number(str(geo.get("longitude"))))
            img = o.get("image")
            if img:
                imgs = img if isinstance(img, list) else [img]
                out.setdefault("images", [i.get("url") if isinstance(i, dict) else i for i in imgs])
        for v in o.values():
            if isinstance(v, (dict, list)):
                walk(v)

    for sc in b.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(sc.string or ""))
        except (ValueError, TypeError):
            continue
    return out


# ── label → value pairs ─────────────────────────────────────────────────

def label_pairs(b: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for dl in b.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                pairs.append((dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)))
    for tr in b.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            pairs.append((cells[0].get_text(" ", strip=True), cells[1].get_text(" ", strip=True)))
    for el in b.find_all(["li", "p", "div", "span"]):
        kids = [c for c in el.find_all(recursive=False) if c.name]
        text = el.get_text(" ", strip=True)
        if len(text) > 90:
            continue
        if len(kids) == 2 and not kids[0].find_all(recursive=False):
            pairs.append((kids[0].get_text(" ", strip=True), kids[1].get_text(" ", strip=True)))
        # <li>Référence <span>VMC2290</span></li>
        if len(kids) == 1 and el.contents and isinstance(el.contents[0], str) and el.contents[0].strip():
            pairs.append((el.contents[0].strip(), kids[0].get_text(" ", strip=True)))
        m = re.match(r"^([^:：]{2,40})\s*[:：]\s*(.{1,60})$", text)
        if m:
            pairs.append((m.group(1), m.group(2)))
        # "3 chambres", "120 m²", "5ème étage"
        m = re.match(r"^(\d+[\d.,]*)\s*(chambres?|bedrooms?|pi[eè]ces?|rooms?|salles? de bains?|bathrooms?)$", text, re.I)
        if m:
            pairs.append((m.group(2), m.group(1)))
    return pairs


def map_labels(pairs: list[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, value in pairs:
        label = clean(label) or ""
        value = clean(value) or ""
        if not label or not value or len(label) > 45 or len(value) > 120 or value.lower() == label.lower():
            continue
        for key, rx in LABELS:
            if rx.search(label) and key not in out:
                out[key] = value
                break
    return out


# ── agent ───────────────────────────────────────────────────────────────

def find_agent(b: BeautifulSoup, agency_phone: str | None, agency_name: str | None) -> dict:
    """Nearest person name to a tel:/mailto:/wa.me link inside a block that
    looks like an agent card; the agency's own switchboard is not an agent."""
    agency_digits = re.sub(r"\D", "", agency_phone or "")[-8:]
    agency_words = {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", agency_name or "")}
    best: dict = {}
    for a in b.select('a[href^="tel:"], a[href^="mailto:"], a[href*="wa.me/"], a[href*="api.whatsapp.com"]'):
        block = a
        for _ in range(5):
            if block.parent is None:
                break
            block = block.parent
            text = block.get_text(" ", strip=True)
            if len(text) > 600:
                break
            if not AGENT_SECTION.search(text) and not block.find(class_=re.compile(r"agent|nego|advisor|conseill|contact", re.I)):
                continue
            # The agency's own contact block (name + street address) is not an agent card.
            if agency_words and len(agency_words & {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)}) >= 2 \
                    and re.search(r"\b(avenue|boulevard|bd|rue|place|quai|all[ée]e|98000)\b", text, re.I):
                continue
            names = [n for n in PERSON.findall(text)
                     if not NOT_PERSON.search(n) and not ({w.lower() for w in n.split()} & agency_words)]
            if not names:
                continue
            name = ROLE_WORDS.sub("", names[0]).strip()
            if len(name.split()) < 2:
                continue
            found = {"agent_name": name}
            for x in block.select('a[href^="tel:"]'):
                d = re.sub(r"\D", "", x["href"])
                if d and d[-8:] != agency_digits:
                    found["agent_phone"] = htmllib.unescape(x["href"][4:]).strip()
                    break
            for x in block.select('a[href^="mailto:"]'):
                found["agent_email"] = htmllib.unescape(x["href"][7:]).split("?")[0].strip()
                break
            for x in block.select('a[href*="wa.me/"], a[href*="api.whatsapp.com"]'):
                m = re.search(r"(?:wa\.me/|phone=)(\+?\d{6,})", x["href"])
                if m:
                    found["agent_whatsapp"] = m.group(1)
                    break
            if len(found) > len(best):
                best = found
            break
    return best


# ── detail ──────────────────────────────────────────────────────────────

def images(b: BeautifulSoup, base: str) -> list[str]:
    urls: list[str] = []
    for el in b.find_all(["img", "a", "source", "div"]):
        for attr in ("data-src", "data-lazy", "data-original", "data-full", "data-large", "href", "src", "srcset",
                     "data-srcset", "data-bg", "style"):
            v = el.get(attr)
            if not v:
                continue
            for u in re.findall(r"((?:https?:)?//[^\s\"')]+|/[^\s\"')]+)", v):
                if IMG_EXT.search(u) and not IMG_JUNK.search(u):
                    full = urljoin(base, u)
                    if full not in urls:
                        urls.append(full)
    return urls[:60]


def transaction_from(url: str, text: str, labels: dict) -> str | None:
    blob = " ".join([url.lower(), (labels.get("transaction") or "").lower()])
    if re.search(r"locat(?!if)|louer|rent|to-let|affitt|аренд|€\s*/\s*(?:mois|month)\b", blob):
        return "rent"
    if re.search(r"vente|vendre|acheter|sale|buy|vendita|продаж", blob):
        return "sale"
    return None


def transaction_from_text(text: str, labels: dict) -> str | None:
    """Weak signal: a sale description can mention the current tenant's rent."""
    if "rent_price" in labels or re.search(r"€\s*/\s*mois|per month|/month|par mois", text, re.I):
        return "rent"
    return None


# Class tokens of blocks that are not the listing. Matched as whole
# hyphen/underscore-separated words: "no-share" or "card-footer-price" wrappers
# can hold the listing itself.
NOISE = re.compile(r"^(?:[\w]+[-_])*(?:similar|related|recommend\w*|other-propert\w*|autres|also-like|cookies?|newsletter|"
                   r"menu|navbar|breadcrumbs?|modal|popup|social)(?:[-_][\w-]+)?$|"
                   r"^(?:site|page|main|global)?[-_]?footer$|^(?:share|sharing|social-share|share-\w+)$", re.I)
ITB_ID = re.compile(r"/(\d{5,6})(?:[-_/]|$)")


RAW_IMG = re.compile(r"(https?:)?(//[^\s\"'()<>]+?\.(?:jpe?g|png|webp))(?:\?[^\s\"'()<>]*)?", re.I)


def raw_images(html: str, base: str) -> list[str]:
    """Gallery URLs that only live in JS (sliders, JSON blobs), same site or CDN."""
    out: list[str] = []
    for m in RAW_IMG.finditer(html.replace("\\/", "/")):
        u = urljoin(base, (m.group(1) or "https:") + m.group(2))
        if not IMG_JUNK.search(u) and u not in out:
            out.append(u)
    return out


def parse_detail(html: str, url: str, cfg: dict, agency: dict, hint: str | None = None) -> dict:
    b = soup(html)
    ld = jsonld(b)
    js_photos = raw_images(html, url)
    # Gallery picked by CSS before page chrome is stripped (some sites put it in <header>).
    gallery: list[str] = []
    for el in b.select((cfg.get("overrides") or {}).get("photos") or ":not(*)"):
        for img in [el] if el.name == "img" else el.find_all("img"):
            src = img.get("data-src") or img.get("data-lazy") or img.get("src")
            if src and not IMG_JUNK.search(src) and urljoin(url, src) not in gallery:
                gallery.append(urljoin(url, src))
    for t in b.find_all(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        if not t.decomposed:
            t.decompose()
    # Site-specific blocks that are not the listing (config "remove": [css, ...]).
    for css in cfg.get("remove") or []:
        for t in b.select(css):
            if not t.decomposed:
                t.decompose()
    # "Similar properties" blocks would leak their bedrooms/prices into ours.
    total = len(b.get_text(" ", strip=True)) or 1
    for t in b.find_all(True, attrs={"class": NOISE}):
        if t.decomposed or t.name in ("body", "html", "main"):
            continue
        # Never the block that holds the listing itself.
        if t.find("h1") or len(t.get_text(" ", strip=True)) > 0.4 * total:
            continue
        t.decompose()
    labels = map_labels(label_pairs(b))
    text = b.get_text(" ", strip=True)
    ov = cfg.get("overrides") or {}

    def sel(field: str) -> str | None:
        if field in ov:
            el = b.select_one(ov[field])
            return clean(el.get_text(" ", strip=True)) if el else None
        return None

    og = {m.get("property"): m.get("content") for m in b.select("meta[property^='og:']")}
    title = sel("title") or clean(ld.get("title")) or clean((b.find("h1") or b.new_tag("x")).get_text(" ")) or clean(og.get("og:title"))
    desc = sel("description") or clean(ld.get("description")) or clean(og.get("og:description"))
    if not desc:
        blocks = sorted((p.get_text(" ", strip=True) for p in b.find_all(["p", "div"]) if not p.find(["p", "div"])),
                        key=len, reverse=True)
        desc = clean(blocks[0]) if blocks and len(blocks[0]) > 150 else None

    # URL/label first, then the index the listing was found on (sale vs rent
    # pages), then page text; a config can pin it for single-type sites.
    tx_text = (sel("transaction") or "").lower()
    transaction = cfg.get("transaction") or transaction_from(tx_text, "", {}) or transaction_from(url, text, labels) or hint \
        or transaction_from_text(text, labels)
    # A business lease for sale also shows its monthly rent: pick by transaction.
    price_text = sel("price") or (labels.get("rent_price") or labels.get("price") if transaction == "rent"
                                  else labels.get("price") or (labels.get("rent_price") if transaction is None else None))
    price = parse_number(price_text) if price_text else ld.get("price")
    # The configured price element says "on request" (no digits): don't hunt for other amounts (fees etc.).
    on_request = bool(ov.get("price")) and bool(price_text) and not re.search(r"\d", price_text)
    if price is None and not on_request:
        # Templates mark the headline price with a price/prix class.
        for el in b.find_all(True, attrs={"class": re.compile(r"price|prix|prezzo", re.I)}):
            t = el.get_text(" ", strip=True)
            if re.search(r"\d", t) and re.search(r"€|eur", t, re.I) and len(t) < 60 and not re.search(r"/\s*mois|month|charges", t, re.I):
                price = parse_number(t)
                break
    if price is None and not on_request:
        amounts = [parse_number(m.group(1) or m.group(2)) for m in
                   re.finditer(r"(\d[\d\s.,\u00a0\u202f]{3,})\s*(?:€|eur\b)|€\s*(\d[\d\s.,\u00a0\u202f]{3,})", text[:6000], re.I)]
        amounts = [a for a in amounts if a]
        if amounts:
            price = max(amounts) if transaction == "sale" else amounts[0]
    if transaction is None and price:
        if re.search(r"par mois|/\s*mois|per month|/\s*month|monthly|mensuel", text[:4000], re.I):
            transaction = "rent"
        elif price >= 150000:
            transaction = "sale"
    # "On request" only when no figure was found: menus and footers often
    # say "estimation sur demande" etc.
    por = (bool(price_text) and bool(re.search(r"sur demande|on request|on application|p\.o\.a", price_text, re.I))) or (
        price is None and bool(re.search(r"prix sur demande|price on request|price upon request|prix: sur demande", text[:5000], re.I)))
    if por and not price_text:
        price = None

    def num(key: str):
        v = sel(key) or labels.get(key)
        return parse_number(v) if v else ld.get(key)

    living = num("living_hab") or num("living")
    if living is None:
        m = re.search(r"(\d[\d.,]*)\s*(?:m²|m2|sqm|sq\.? ?m)\b", text, re.I)
        living = parse_number(m.group(1)) if m else None
    floor_raw = sel("floor") or labels.get("floor")
    floor = 0 if floor_raw and re.match(r"(rdc|rez|ground|garden)", floor_raw, re.I) else (
        int(parse_number(floor_raw)) if floor_raw and parse_number(floor_raw) is not None else ld.get("floor"))

    flags = keyword_flags(f"{title or ''}\n{desc or ''}")
    tt = f"{title or ''} {og.get('og:title') or ''}"
    m = re.search(r"(\d+)\s*(?:-|\s)?\s*(?:pi[eè]ces|rooms?|locali|комнат)", tt, re.I)
    title_rooms = int(m.group(1)) if m else (1 if re.search(r"\bstudio\b", tt, re.I) else None)
    m = re.search(r"(\d+)\s*(?:-|\s)?\s*(?:chambres?|bed(?:room)?s?|camere|спал)", tt, re.I)
    title_beds = int(m.group(1)) if m else (0 if re.search(r"\bstudio\b", tt, re.I) else None)
    agent = {k: v for k, v in ld.items() if k.startswith("agent_") and v}
    if not agent:
        agent = find_agent(b, agency.get("phone"), agency.get("name"))
    photos = [u for u in (ld.get("images") or []) if isinstance(u, str)] or gallery[:60] or images(b, url)
    if len(photos) < 2:
        # Keep URLs that look like listing media (share a path with the ones we have, or mention the id).
        lid = re.search(r"\d{4,}", urlparse(url).path)
        extra = [u for u in js_photos if (lid and lid.group(0) in u) or re.search(r"/(?:photos?|images?|media|uploads|annonces?|biens?|propert)", u, re.I)]
        photos = list(dict.fromkeys(photos + extra))[:60]
    as_int = lambda v: int(v) if isinstance(v, (int, float)) else None  # noqa: E731

    listing = {
        "source_url": url,
        "detail": True,
        "transaction_type": transaction,
        "price": price if not por else None,
        "price_on_request": price is None,
        "currency": ld.get("currency") or "EUR",
        "rent_period": "month" if transaction == "rent" else None,
        "title": title,
        "description": desc,
        "property_type": (clean(labels.get("type")) or "").lower() or None,
        "rooms": as_int(num("rooms")) if num("rooms") is not None else title_rooms,
        "bedrooms": as_int(num("bedrooms")) if num("bedrooms") is not None else title_beds,
        "bathrooms": as_int(num("bathrooms")),
        "living_area_sqm": living,
        "terrace_sqm": num("terrace"),
        "floor": floor if isinstance(floor, int) else None,
        "parking": as_int(num("parking")) if labels.get("parking") and parse_number(labels["parking"]) is not None
                   else (1 if labels.get("parking") or flags.get("parking") else None),
        "cellar": True if labels.get("cellar") or flags.get("cellar") else None,
        "sea_view": True if flags.get("sea_view") else None,
        "building_name": sel("building") or labels.get("building"),
        "quarter": sel("quarter") or labels.get("quarter") or title,
        "lat": ld.get("lat"),
        "lng": ld.get("lng"),
        "external_ref": sel("reference") or labels.get("reference"),
        "photo_urls": photos,
        "agency_phone": agency.get("phone"),
        "agency_email": agency.get("email"),
        **{k: v for k, v in agent.items() if k in ("agent_name", "agent_phone", "agent_email", "agent_whatsapp")},
        "extra": {k: v for k, v in {
            "charges": parse_number(labels["charges"]) if labels.get("charges") else None,
            # Immotoolbox sites share the CIM portal's listing ids: exact merge key.
            # Worker only uses it against the same agency's CIM listing.
            "cim_id": (ITB_ID.search(urlparse(url).path) or [None, None])[1],
        }.items() if v is not None},
    }
    if listing["external_ref"]:
        listing["external_ref"] = re.sub(r"^(?:r[éeè]f(?:[ée]rence)?\.?|ref\.?)\s*[:#]?\s*", "",
                                         listing["external_ref"], flags=re.I).strip() or None
    return listing


# ── index ───────────────────────────────────────────────────────────────

LINK_ATTRS = ("onclick", "data-href", "data-url", "data-link")
QUOTED_URL = re.compile(r"""['"]((?:https?://|/)[^'"\s]+)['"]""")


def card_links(b: BeautifulSoup) -> list[str]:
    """hrefs, plus cards that navigate by script: onclick="window.open('…')",
    data-href / data-url attributes."""
    out = [a["href"] for a in b.find_all("a", href=True)]
    for el in b.find_all(lambda t: any(t.has_attr(k) for k in LINK_ATTRS)):
        for k in LINK_ATTRS:
            v = el.get(k)
            if not v:
                continue
            out += QUOTED_URL.findall(v) if k == "onclick" else [v]
    return out


def page_url(start: str, u: str) -> str | None:
    from urllib.parse import parse_qsl, urlencode
    pu, ps = urlparse(u), urlparse(start)
    m = re.search(r"/page/(\d+)", pu.path)
    if m:
        return f"{pu.scheme}://{pu.netloc}{pu.path}" + (f"?{ps.query}" if ps.query else "")
    q = dict(parse_qsl(pu.query))
    num = next(((k, q[k]) for k in ("page", "p", "pg", "paged") if q.get(k, "").isdigit()), None)
    if not num:
        return None
    keep = dict(parse_qsl(ps.query))
    keep[num[0]] = num[1]
    return f"{pu.scheme}://{pu.netloc}{pu.path}?{urlencode(keep)}"


def crawl_index(f: PoliteFetcher, cfg: dict) -> list[dict]:
    """Every listing URL across the configured index pages, following
    rel=next / numbered pagination within the same index path — or, for
    JS-rendered grids, every matching URL in the sitemap."""
    pattern = re.compile(cfg["listing_pattern"])
    if cfg.get("index_source") == "sitemap":
        from scraper.autoconfig import sitemap_urls
        plain = f if isinstance(f, PoliteFetcher) else PoliteFetcher()  # raw XML, not a rendered viewer
        urls = [u for u in dict.fromkeys(sitemap_urls(plain, cfg["website"], limit=20)) if pattern.search(u)]
        if not urls:
            raise RuntimeError("sitemap returned no listing URLs")  # never report an empty index as complete
        return [{"source_url": u, "transaction_hint": None} for u in urls]
    max_pages = cfg.get("max_pages", 60)
    found: dict[str, dict] = {}
    dead_starts: list[str] = []
    all_starts = [u for v in (cfg.get("index_urls") or {}).values() for u in v]
    for transaction, starts in (cfg.get("index_urls") or {}).items():
        for start in starts:
            queue, seen_pages = [start], set()
            while queue and len(seen_pages) < max_pages:
                page = queue.pop(0)
                if page in seen_pages:
                    continue
                seen_pages.add(page)
                try:
                    h = f.get(page)
                except NotFound:
                    if page == start:
                        dead_starts.append(start)
                    continue  # a dead "next page" link just ends that pagination
                b = soup(h)
                new = 0
                for href in card_links(b):
                    u = urljoin(page, href).split("#")[0]
                    if pattern.search(u) and u not in found:
                        found[u] = {"source_url": u, "transaction_hint": transaction}
                        new += 1
                nxt = b.select_one('link[rel="next"], a[rel="next"]')
                raw = [urljoin(page, nxt["href"])] if nxt and nxt.get("href") else []
                base_path = urlparse(start).path.rstrip("/")
                for a in b.find_all("a", href=True):
                    u = urljoin(page, a["href"])
                    if urlparse(u).path.startswith(base_path) and re.search(r"(?:[?&](?:page|p|pg|paged)=\d+|/page/\d+)", u):
                        raw.append(u)
                # Sort options and #anchors multiply one page into dozens of
                # URLs: keep the start page's own query + the page number only.
                cands = list(dict.fromkeys(filter(None, (page_url(start, u) for u in raw))))
                if new or page == start:
                    queue += [c for c in cands if c not in seen_pages and c not in queue]
    if all_starts and len(dead_starts) == len(all_starts):
        raise NotFound(f"every index page is gone: {dead_starts}")
    return list(found.values())
