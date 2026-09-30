"""
Example site parser: lorenza (illustrative Monaco portal).

Same TEMPLATE disclaimer as parsers/miells_parser.py — the __NEXT_DATA__
paths here are placeholders. Deliberately shaped differently from the
miells example (flat property array instead of a paginated search
response, coordinates as a [lat, lng] tuple instead of an object, area in
square feet requiring conversion) to show that each parser only needs to
handle ITS OWN portal's quirks; BaseSiteParser handles everything else.
"""

from __future__ import annotations

import json

from parsers.base_parser import BaseSiteParser, ParserError
from dedup.cluster_logic import ScrapedListing

BASE_URL = "https://www.lorenza-realestate.mc"
SEARCH_PATH = "/listings/monaco"

SQFT_TO_SQM = 0.092903


class Parser(BaseSiteParser):
    site_name = "lorenza"

    def get_listing_urls(self) -> list[str]:
        self.page.goto(f"{BASE_URL}{SEARCH_PATH}", timeout=30_000, wait_until="domcontentloaded")
        next_data = self.extract_next_data()

        # This portal returns every active listing in one flat array on
        # the search page (no pagination) — adjust if the real site paginates.
        properties = self.dig(next_data, "props", "pageProps", "properties", required=False) or []

        urls = []
        for prop in properties:
            ref = prop.get("reference")
            if ref:
                urls.append(f"{BASE_URL}/listings/monaco/{ref}")
        return urls

    def parse_listing(self, next_data: dict, url: str) -> ScrapedListing:
        listing = self.dig(next_data, "props", "pageProps", "property")

        # This portal encodes coordinates as a [lat, lng] pair, not an object.
        geo = listing.get("geo")
        if not geo or len(geo) != 2:
            raise ParserError(f"Listing at {url} has malformed/missing geo coordinates.")
        latitude, longitude = geo

        # Area comes back in square feet here — normalize to sqm so it
        # clusters correctly against every other portal.
        area_sqft = listing.get("areaSqft")
        if not area_sqft:
            raise ParserError(f"Listing at {url} has no floor area — cannot cluster it.")
        living_area_sqm = round(float(area_sqft) * SQFT_TO_SQM, 1)

        price_block = listing.get("pricing") or {}
        price = price_block.get("amount")
        if price is None:
            raise ParserError(f"Listing at {url} has no price.")

        agency = listing.get("listingAgency") or {}

        return ScrapedListing(
            site_name=self.site_name,
            title=listing.get("headline") or "Untitled listing",
            description=listing.get("fullDescription"),
            price=float(price),
            currency=price_block.get("currency") or "EUR",
            living_area_sqm=living_area_sqm,
            latitude=float(latitude),
            longitude=float(longitude),
            district=listing.get("neighborhood"),
            agency_name=agency.get("displayName") or "Lorenza Real Estate",
            original_listing_url=url,
            agent_name=agency.get("agentName"),
            agent_phone=agency.get("agentPhone"),
            agent_email=agency.get("agentEmail"),
            raw_next_data_json=json.dumps(listing),
        )
