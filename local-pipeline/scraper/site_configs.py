"""
Monaco/Riviera Local Pipeline — Site Configuration Matrix
==============================================================
Maps each target portal to one of 4 structural Archetypes and the fields
`parsers/dynamic_parser.py` needs to extract a listing from it, so adding
site #16..#100 is a config entry here, not a new Python file.

    Archetype A — IMMOTOOLBOX_API      regional API/aggregator backend,
                                        clean nested JSON, no rendering needed
    Archetype B — FRAMEWORK_HYDRATION  Next.js/__NEXT_DATA__ or
                                        Nuxt.js/__NUXT__ hydration state
    Archetype C — BOUTIQUE_STATIC      standalone WordPress/custom site;
                                        coords in an inline JS map marker,
                                        or a static HTML spec table
    Archetype D — CLASSIFIED_META      large classified portal; coords and
                                        core fields via <meta property="og:*">
                                        tags. See requires_tos_review below.

IMPORTANT — every dotted JSON path, CSS selector, and regex below is a
best-effort template based on each portal's *publicly documented or
typically-observed* structure. Before running a config against the real
site, inspect one live listing page (devtools Network/Elements tab, or the
API response directly) and correct any path that doesn't match — these
are a scaffold to populate from, not guaranteed-exact selectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Archetype(str, Enum):
    IMMOTOOLBOX_API = "immotoolbox_api"          # A
    FRAMEWORK_HYDRATION = "framework_hydration"    # B
    BOUTIQUE_STATIC = "boutique_static"              # C
    CLASSIFIED_META = "classified_meta"                # D


@dataclass(frozen=True)
class SiteConfig:
    slug: str                 # must match `sites.name` in the staging DB
    domain: str
    display_name: str
    archetype: Archetype

    # -- Archetype A: regional API layer (ImmoToolBox/Apimo-style) ----------
    api_search_url: Optional[str] = None            # returns a JSON list of active listings
    api_results_path: Optional[str] = None            # dotted path to the array, if nested (None = response IS the array)
    api_id_field: str = "id"                            # dotted path (within one item) to its listing id
    api_listing_url_template: Optional[str] = None        # 2nd-call JSON endpoint for full detail, if the search item is abbreviated
    public_url_template: Optional[str] = None               # human-facing page URL template, "{domain}"/"{id}" placeholders

    # -- Archetype B: JS framework hydration ---------------------------------
    search_url: Optional[str] = None
    hydration_var: Optional[str] = None                 # "__NEXT_DATA__" | "__NUXT__"
    listing_list_path: Optional[str] = None               # dotted path to the search-results array
    listing_url_field: Optional[str] = None                 # dotted path (within one item) to its slug/id
    listing_url_template: Optional[str] = None                # "{domain}"/"{value}" placeholders
    detail_hydration_path: Optional[str] = None                  # dotted path INTO the detail page's hydration blob

    # -- Archetype C: boutique static / inline JS map ------------------------
    listing_index_url: Optional[str] = None
    listing_link_selector: Optional[str] = None            # CSS selector for listing <a> tags on the index page
    map_marker_patterns: tuple[str, ...] = field(default_factory=tuple)  # regex, 2 capture groups = (lat, lng)
    static_table_field_labels: dict[str, str] = field(default_factory=dict)  # dest_field -> row label text, for <table>/<dl>-shaped spec sheets
    detail_field_selectors: dict[str, str] = field(default_factory=dict)     # dest_field -> CSS selector, for sites that use their own uniquely-classed elements instead of a generic table (e.g. <div class="immobileInfo price">)
    detail_field_regex: dict[str, str] = field(default_factory=dict)          # dest_field -> optional regex applied to that selector's text (1 capture group); raw text is used if no pattern is given

    # -- Archetype D: classified portal / meta tags ---------------------------
    meta_tag_map: dict[str, str] = field(default_factory=dict)  # dest_field -> og:/meta property name
    price_selector: Optional[str] = None
    price_regex: Optional[str] = None

    # -- shared across every archetype -----------------------------------------
    field_map: dict[str, str] = field(default_factory=dict)    # dest_field -> dotted path (Archetypes A & B only)
    currency_default: str = "EUR"
    agency_name_default: Optional[str] = None
    request_delay_range: tuple[float, float] = (1.8, 4.5)
    requires_tos_review: bool = False
    notes: str = ""


# ----------------------------------------------------------------------------
# Archetype A — ImmoToolBox / Local Monaco API Layer
# ----------------------------------------------------------------------------

_ARCHETYPE_A = [
    # NOTE: montecarlo_realestate was originally scaffolded here as an
    # Archetype A guess. Live inspection (2026-09-22) showed it's actually
    # server-rendered with coordinates in an inline <script> tag, not an
    # API — it has been moved to _ARCHETYPE_C below with verified selectors.
    SiteConfig(
        slug="property_for_sale_monaco",
        domain="www.property-for-sale-monaco.com",
        display_name="Property For Sale Monaco",
        archetype=Archetype.IMMOTOOLBOX_API,
        api_search_url="https://api.immotoolbox.com/v2/agencies/pfsm/listings?status=active",
        api_results_path="results",
        api_id_field="id",
        api_listing_url_template="https://api.immotoolbox.com/v2/agencies/pfsm/listings/{id}",
        public_url_template="https://www.property-for-sale-monaco.com/property/{id}",
        field_map={
            "title": "title.en",
            "description": "description.en",
            "price": "price.value",
            "currency": "price.currency",
            "living_area_sqm": "livingArea",
            "latitude": "geo.lat",
            "longitude": "geo.lng",
            "district": "geo.quarter",
            "agency_name": "agency.name",
            "agent_name": "agent.fullName",
            "agent_phone": "agent.phone",
            "agent_email": "agent.email",
        },
    ),
    SiteConfig(
        slug="chambre_immobiliere_monaco",
        domain="www.chambre-immobiliere-monaco.mc",
        display_name="Chambre Immobiliere Monaco",
        archetype=Archetype.IMMOTOOLBOX_API,
        api_search_url="https://api.immotoolbox.com/v2/agencies/cim/listings?status=active",
        api_results_path="results",
        api_id_field="id",
        api_listing_url_template="https://api.immotoolbox.com/v2/agencies/cim/listings/{id}",
        public_url_template="https://www.chambre-immobiliere-monaco.mc/en/annonce/{id}",
        field_map={
            "title": "title.en",
            "description": "description.en",
            "price": "price.value",
            "currency": "price.currency",
            "living_area_sqm": "area.value",
            "latitude": "location.lat",
            "longitude": "location.lng",
            "district": "location.district",
            "agency_name": "agency.name",
            "agent_name": "contact.name",
            "agent_phone": "contact.phone",
            "agent_email": "contact.email",
        },
    ),
    SiteConfig(
        slug="livein_mc",
        domain="www.livein.mc",
        display_name="LiveIn Monaco",
        archetype=Archetype.IMMOTOOLBOX_API,
        api_search_url="https://api.apimo.pro/agencies/livein-mc/properties?status=active",
        api_results_path=None,  # this backend returns a bare JSON array
        api_id_field="reference",
        api_listing_url_template=None,  # search response already has full detail, no 2nd call needed
        public_url_template="https://www.livein.mc/en/property/{id}",
        field_map={
            "title": "title",
            "description": "comment",
            "price": "price",
            "currency": "currency",
            "living_area_sqm": "area",
            "latitude": "town.geo_lat",
            "longitude": "town.geo_lng",
            "district": "town.name",
            "agency_name": "agency.name",
            "agent_name": "contacts.0.name",
            "agent_phone": "contacts.0.phone",
            "agent_email": "contacts.0.email",
        },
        notes="Apimo-backed. Field paths use Apimo's typical property schema — verify against a live response.",
    ),
]


# ----------------------------------------------------------------------------
# Archetype B — Global Aggregate Framework (__NEXT_DATA__ / __NUXT__)
# ----------------------------------------------------------------------------

_ARCHETYPE_B = [
    SiteConfig(
        slug="luxuryestate",
        domain="www.luxuryestate.com",
        display_name="LuxuryEstate",
        archetype=Archetype.FRAMEWORK_HYDRATION,
        search_url="https://www.luxuryestate.com/en/monaco",
        hydration_var="__NEXT_DATA__",
        listing_list_path="props.pageProps.searchResults.results",
        listing_url_field="seoUrl",
        listing_url_template="https://{domain}{value}",
        detail_hydration_path="props.pageProps.listing",
        field_map={
            "title": "title",
            "description": "description",
            "price": "price.amount",
            "currency": "price.currency",
            "living_area_sqm": "surface.value",
            "latitude": "geoPosition.latitude",
            "longitude": "geoPosition.longitude",
            "district": "location.district",
            "agency_name": "advertiser.agencyName",
            "agent_name": "advertiser.name",
            "agent_phone": "advertiser.phone",
            "agent_email": "advertiser.email",
        },
    ),
    SiteConfig(
        slug="properstar",
        domain="www.properstar.com",
        display_name="Properstar",
        archetype=Archetype.FRAMEWORK_HYDRATION,
        search_url="https://www.properstar.com/monaco/buy",
        hydration_var="__NUXT__",
        listing_list_path="state.search.results.items",
        listing_url_field="id",
        listing_url_template="https://{domain}/monaco/buy/listing-{value}",
        detail_hydration_path="state.listing.detail",
        field_map={
            "title": "headline",
            "description": "longDescription",
            "price": "pricing.amount",
            "currency": "pricing.currency",
            "living_area_sqm": "attributes.livingArea",
            "latitude": "geo.latitude",
            "longitude": "geo.longitude",
            "district": "geo.neighbourhood",
            "agency_name": "listedBy.agencyName",
            "agent_name": "listedBy.agentName",
            "agent_phone": "listedBy.agentPhone",
            "agent_email": "listedBy.agentEmail",
        },
    ),
    SiteConfig(
        slug="green_acres_mc",
        domain="mc.green-acres.com",
        display_name="Green Acres Monaco",
        archetype=Archetype.FRAMEWORK_HYDRATION,
        search_url="https://mc.green-acres.com/en/monaco/",
        hydration_var="__NEXT_DATA__",
        listing_list_path="props.pageProps.ads.items",
        listing_url_field="slug",
        listing_url_template="https://{domain}/en/ad/{value}",
        detail_hydration_path="props.pageProps.ad",
        field_map={
            "title": "title",
            "description": "description",
            "price": "price",
            "currency": "currencyCode",
            "living_area_sqm": "livingArea",
            "latitude": "coordinates.lat",
            "longitude": "coordinates.lng",
            "district": "district",
            "agency_name": "agency.name",
            "agent_name": "agency.contactName",
            "agent_phone": "agency.phone",
            "agent_email": "agency.email",
        },
    ),
    SiteConfig(
        slug="barnes_international",
        domain="www.barnes-international.com",
        display_name="Barnes International",
        archetype=Archetype.FRAMEWORK_HYDRATION,
        search_url="https://www.barnes-international.com/monaco/list",
        hydration_var="__NEXT_DATA__",
        listing_list_path="props.pageProps.searchResults.hits",
        listing_url_field="slug",
        listing_url_template="https://{domain}/property/{value}",
        detail_hydration_path="props.pageProps.property",
        field_map={
            "title": "title",
            "description": "description",
            "price": "price.value",
            "currency": "price.currency",
            "living_area_sqm": "surfaceArea",
            "latitude": "location.latitude",
            "longitude": "location.longitude",
            "district": "location.district",
            "agency_name": "office.name",
            "agent_name": "office.contactName",
            "agent_phone": "office.phone",
            "agent_email": "office.email",
        },
    ),
]


# ----------------------------------------------------------------------------
# Archetype C — Direct Boutique Agency Portals
# ----------------------------------------------------------------------------

_GOOGLE_MAPS_MARKER_PATTERNS = (
    r"new\s+google\.maps\.LatLng\(\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*\)",
    r"new\s+google\.maps\.Marker\(\s*\{[^}]*?position:\s*\{\s*lat:\s*(-?\d{1,3}\.\d+)\s*,\s*lng:\s*(-?\d{1,3}\.\d+)",
)
_LEAFLET_MARKER_PATTERNS = (
    r"L\.marker\(\s*\[\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*\]",
    r"L\.latLng\(\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*\)",
)
# TrovaCasa CMS (montecarlo-realestate.com and, per its own footer, several
# other Monaco/Riviera agency sites): coordinates are server-rendered as
# plain JS variables just before the map is lazy-initialized, not passed
# through a google.maps.* / Leaflet constructor call.
_TROVACASA_VAR_PATTERNS = (
    r"var\s+latitude\s*=\s*(-?\d{1,3}\.\d+)\s*;\s*var\s+longitude\s*=\s*(-?\d{1,3}\.\d+)\s*;",
)

_ARCHETYPE_C = [
    SiteConfig(
        # Verified live 2026-09-22 against https://www.montecarlo-realestate.com.
        # This site is a Monaco listings AGGREGATOR, not a single agency — the
        # `.agenzia__name` element holds whichever agency actually placed the
        # listing (e.g. "John Taylor" on a live listing checked during review),
        # not "Monte-Carlo Real Estate" itself.
        slug="montecarlo_realestate",
        domain="www.montecarlo-realestate.com",
        display_name="Monte-Carlo Real Estate",
        archetype=Archetype.BOUTIQUE_STATIC,
        listing_index_url="https://www.montecarlo-realestate.com/maisons-et-appartements-en-vente/monaco",
        listing_link_selector="a.card__title.js_link_immobile",
        map_marker_patterns=_TROVACASA_VAR_PATTERNS,
        # This site renders price/area/district/reference as their own
        # uniquely-classed <div>s (a TrovaCasa CMS convention), not a
        # <table> — static_table_field_labels wouldn't match this markup,
        # so these use detail_field_selectors instead (see dynamic_parser.py).
        detail_field_selectors={
            "title": "h1.immobile__title",
            "price": ".immobileInfo.price",
            "living_area_sqm": ".immobileInfo.info",
            "district": ".immobileInfo.info",
            "agency_name": ".agenzia__name",
            "agent_phone": "a[href^='tel:']",
        },
        detail_field_regex={
            # ".immobileInfo.price" text is e.g. "4 800 000 €" — capture the
            # digit run (with its internal spaces, which _coerce_price strips).
            "price": r"([\d\s]+)\s*€",
            # ".immobileInfo.info" text is e.g. "Les Ligures (Jardin Exotique) 90 m² 2 Pièces" —
            # anchor on the m² unit so a numeral inside the residence name
            # (e.g. "Résidence 21") is never mistaken for the floor area.
            "living_area_sqm": r"(\d+(?:\.\d+)?)\s*m²",
            # Same source text — the district is the parenthesized segment.
            "district": r"\(([^)]+)\)",
        },
        # No per-agent name/email is exposed on this site, only the agency's
        # switchboard number via a tel: link — left unset rather than
        # guessed, per the safe-gate's "never fabricate a field" rule.
        request_delay_range=(2.0, 4.5),
        notes=(
            "TrovaCasa CMS. Verified 2026-09-22: no __NEXT_DATA__/__NUXT__, no JSON API — "
            "plain server-rendered HTML. Some listings omit map coordinates entirely "
            "(var latitude/longitude render as `undefined`, seen on at least one live "
            "listing) — those are correctly skipped by the safe-gate for lacking a "
            "cluster key, not a bug. Listing index paginates via ?page=N."
        ),
    ),
    SiteConfig(
        slug="ageprim",
        domain="www.ageprim.com",
        display_name="AgePrim",
        archetype=Archetype.BOUTIQUE_STATIC,
        listing_index_url="https://www.ageprim.com/en/properties",
        listing_link_selector="a.property-card__link, a.property-item__link",
        map_marker_patterns=_GOOGLE_MAPS_MARKER_PATTERNS,
        static_table_field_labels={
            "living_area_sqm": "Surface",
            "price": "Price",
            "district": "Location",
        },
        agency_name_default="AgePrim",
    ),
    SiteConfig(
        slug="lorenzavonstein",
        domain="www.lorenzavonstein.com",
        display_name="Lorenza von Stein",
        archetype=Archetype.BOUTIQUE_STATIC,
        listing_index_url="https://www.lorenzavonstein.com/properties/monaco",
        listing_link_selector="a.property-link, .property-grid a[href*='/property/']",
        map_marker_patterns=_LEAFLET_MARKER_PATTERNS + _GOOGLE_MAPS_MARKER_PATTERNS,
        static_table_field_labels={
            "living_area_sqm": "Living area",
            "price": "Price",
            "district": "Quarter",
        },
        agency_name_default="Lorenza von Stein",
    ),
    SiteConfig(
        slug="dotta",
        domain="www.dotta.mc",
        display_name="Dotta Immobilier",
        archetype=Archetype.BOUTIQUE_STATIC,
        listing_index_url="https://www.dotta.mc/en/buy",
        listing_link_selector="a.o-property-card__link",
        map_marker_patterns=_GOOGLE_MAPS_MARKER_PATTERNS,
        static_table_field_labels={
            "living_area_sqm": "Surface",
            "price": "Price",
            "district": "District",
        },
        agency_name_default="Dotta Immobilier",
    ),
    SiteConfig(
        slug="lacosta_properties_monaco",
        domain="www.lacosta-properties-monaco.com",
        display_name="LaCosta Properties Monaco",
        archetype=Archetype.BOUTIQUE_STATIC,
        listing_index_url="https://www.lacosta-properties-monaco.com/en/properties-for-sale",
        listing_link_selector="a.property-listing__link, .listing-card a",
        map_marker_patterns=_GOOGLE_MAPS_MARKER_PATTERNS + _LEAFLET_MARKER_PATTERNS,
        static_table_field_labels={
            "living_area_sqm": "Living Space",
            "price": "Price",
            "district": "Area",
        },
        agency_name_default="LaCosta Properties Monaco",
    ),
]


# ----------------------------------------------------------------------------
# Archetype D — High-Security Classified Portals
#
# These are large third-party platforms with active anti-bot protection
# (Cloudflare/DataDome) and, in most cases, Terms of Service that restrict
# automated access. `requires_tos_review=True` makes the orchestrator log a
# loud warning before ever running one — confirm you have a lawful basis
# (their API/data-licensing program, an affiliate agreement, or your own
# legal review of applicable law and their ToS) before enabling these in a
# production run. Only generic, publicly-documented extraction (Open Graph
# meta tags) is implemented here — no CAPTCHA-solving, proxy-rotation, or
# other anti-bot-defeat logic is included by design.
# ----------------------------------------------------------------------------

_ARCHETYPE_D = [
    SiteConfig(
        slug="leboncoin",
        domain="www.leboncoin.fr",
        display_name="Le Bon Coin",
        archetype=Archetype.CLASSIFIED_META,
        search_url="https://www.leboncoin.fr/recherche?category=9&locations=Monaco",
        listing_link_selector="a[data-qa-id='aditem_container']",
        meta_tag_map={
            "title": "og:title",
            "description": "og:description",
            "latitude": "og:latitude",
            "longitude": "og:longitude",
        },
        price_selector="[data-qa-id='adview_price']",
        price_regex=r"([\d\s]+)\s*€",
        request_delay_range=(3.0, 7.0),
        requires_tos_review=True,
        notes="General classifieds, not Monaco-specific. High false-positive rate expected; confirm licensing before use.",
    ),
    SiteConfig(
        slug="seloger",
        domain="www.seloger.com",
        display_name="SeLoger",
        archetype=Archetype.CLASSIFIED_META,
        search_url="https://www.seloger.com/list.htm?projects=2&types=1&places=%5B%7Bci%3A990%7D%5D",
        listing_link_selector="a.c-pa-link, a[data-testid='listing-card-link']",
        meta_tag_map={
            "title": "og:title",
            "description": "og:description",
            "latitude": "og:latitude",
            "longitude": "og:longitude",
        },
        price_selector="[data-testid='price']",
        price_regex=r"([\d\s]+)\s*€",
        request_delay_range=(3.0, 7.0),
        requires_tos_review=True,
    ),
    SiteConfig(
        slug="zoopla",
        domain="www.zoopla.co.uk",
        display_name="Zoopla",
        archetype=Archetype.CLASSIFIED_META,
        search_url="https://www.zoopla.co.uk/for-sale/property/monaco/",
        listing_link_selector="a[data-testid='listing-details-link']",
        meta_tag_map={
            "title": "og:title",
            "description": "og:description",
            "latitude": "place:location:latitude",
            "longitude": "place:location:longitude",
        },
        price_selector="[data-testid='price']",
        price_regex=r"€\s*([\d,]+)",
        currency_default="EUR",
        request_delay_range=(3.0, 7.0),
        requires_tos_review=True,
    ),
]


SITE_CONFIGS: dict[str, SiteConfig] = {
    cfg.slug: cfg for cfg in (*_ARCHETYPE_A, *_ARCHETYPE_B, *_ARCHETYPE_C, *_ARCHETYPE_D)
}
