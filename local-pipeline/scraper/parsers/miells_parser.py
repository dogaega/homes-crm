"""
Example site parser: miells (illustrative Monaco portal).

This is a TEMPLATE showing exactly how a real parser is structured — the
__NEXT_DATA__ JSON paths below (`props.pageProps.searchResults...`,
`props.pageProps.listing...`) are placeholders modeled on a typical
Next.js real-estate site. Before running this against the real
miells.mc, open a listing page in a browser, view source, find the
<script id="__NEXT_DATA__"> tag, and adjust every `self.dig(...)` path
below to match that portal's actual JSON shape.
"""

from __future__ import annotations

import json

from parsers.base_parser import BaseSiteParser, ParserError
from dedup.cluster_logic import ScrapedListing

BASE_URL = "https://www.miells.mc"
SEARCH_PATH = "/en/buy/monaco"


class Parser(BaseSiteParser):
    site_name = "miells"

    def get_listing_urls(self) -> list[str]:
        urls: list[str] = []
        page_num = 1

        while True:
            self.page.goto(
                f"{BASE_URL}{SEARCH_PATH}?page={page_num}",
                timeout=30_000,
                wait_until="domcontentloaded",
            )
            next_data = self.extract_next_data()

            # Adjust to the portal's real results-list path.
            results = self.dig(
                next_data, "props", "pageProps", "searchResults", "items", required=False
            ) or []
            if not results:
                break

            for item in results:
                slug = item.get("slug") or item.get("id")
                if slug:
                    urls.append(f"{BASE_URL}/en/property/{slug}")

            total_pages = self.dig(
                next_data, "props", "pageProps", "searchResults", "totalPages", required=False
            ) or 1
            if page_num >= total_pages:
                break
            page_num += 1

        return urls

    def parse_listing(self, next_data: dict, url: str) -> ScrapedListing:
        listing = self.dig(next_data, "props", "pageProps", "listing")

        coords = listing.get("coordinates") or {}
        latitude = coords.get("lat")
        longitude = coords.get("lng")
        if latitude is None or longitude is None:
            raise ParserError(f"Listing at {url} has no coordinates — cannot cluster it.")

        living_area_sqm = listing.get("livingAreaSqm") or listing.get("surface")
        if not living_area_sqm:
            raise ParserError(f"Listing at {url} has no living area (sqm) — cannot cluster it.")

        price = listing.get("price")
        if price is None:
            raise ParserError(f"Listing at {url} has no price.")

        agent = listing.get("contact") or {}

        return ScrapedListing(
            site_name=self.site_name,
            title=listing.get("title") or "Untitled listing",
            description=listing.get("description"),
            price=float(price),
            currency=listing.get("currency") or "EUR",
            living_area_sqm=float(living_area_sqm),
            latitude=float(latitude),
            longitude=float(longitude),
            district=listing.get("district") or listing.get("quarter"),
            agency_name=listing.get("agencyName") or "Miells",
            original_listing_url=url,
            agent_name=agent.get("name"),
            agent_phone=agent.get("phone"),
            agent_email=agent.get("email"),
            raw_next_data_json=json.dumps(listing),  # audit trail / re-parse if extraction logic changes later
        )
