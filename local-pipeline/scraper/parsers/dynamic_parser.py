"""
Monaco/Riviera Local Pipeline — Configuration-Driven Dynamic Parser
=======================================================================
`DynamicSiteParser` is the single class that scrapes any site listed in
`site_configs.SITE_CONFIGS` without a bespoke `parsers/<site>_parser.py`
file. It routes `get_listing_urls()` / `fetch_listing_payload()` /
`parse_listing()` through one of four Archetype handlers based on the
site's `SiteConfig.archetype`.

Every handler, regardless of archetype, funnels its extracted values
through `normalize_listing()` before returning — that's the safe-gate
(Task 3 of this brief): no matter which archetype produced the raw data,
`ClusterEngine.ingest()` only ever receives a fully-validated, uniformly-
typed `ScrapedListing`. A listing missing coordinates, area, or price is
rejected right here with a clear error, never passed through with a
guessed default.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from parsers.base_parser import BaseSiteParser, ParserError
from dedup.cluster_logic import ScrapedListing
from site_configs import Archetype, SiteConfig

logger = logging.getLogger("dynamic_parser")

REQUIRED_NORMALIZED_FIELDS = ("latitude", "longitude", "living_area_sqm", "price")


# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------

def resolve_path(data: Any, dotted_path: Optional[str]) -> Any:
    """
    Walks a dotted path like "props.pageProps.listing.price.amount" through
    nested dicts/lists. A numeric path segment indexes into a list
    ("contacts.0.email"). Returns None on any miss instead of raising —
    callers decide what's actually required via normalize_listing().
    """
    if not dotted_path:
        return None
    node = data
    for key in dotted_path.split("."):
        if isinstance(node, list):
            if not key.isdigit():
                return None
            idx = int(key)
            if idx >= len(node):
                return None
            node = node[idx]
        elif isinstance(node, dict):
            if key not in node:
                return None
            node = node[key]
        else:
            return None
    return node


def _coerce_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[^\d.,]", "", str(raw))
    cleaned = cleaned.replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _coerce_float(raw: Any) -> Optional[float]:
    """
    Used for area/coordinate values, which are typically a single number
    immediately followed by a unit suffix (e.g. "145 m2", "180m²"). Extracts
    the first standalone numeric token instead of stripping all non-numeric
    characters — stripping would concatenate a stray digit from the unit
    (e.g. the "2" in "m2") straight onto the number, turning "145 m2" into
    1452 instead of 145.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = re.search(r"-?\d+(?:\.\d+)?", str(raw))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def normalize_listing(cfg: SiteConfig, values: dict, url: str) -> ScrapedListing:
    """
    THE SAFE-GATE: every Archetype handler builds a plain `values` dict of
    whatever it could extract, then hands it here. This is the one place
    that validates required fields and coerces types before a
    ScrapedListing is ever constructed — so a regex miss, a moved JSON
    field, or a malformed price string on any of the 100 sites fails loud
    and early, right at this gate, instead of silently corrupting the
    cluster database with e.g. a 0.0 living_area_sqm that then falsely
    clusters unrelated properties together.
    """
    latitude = _coerce_float(values.get("latitude"))
    longitude = _coerce_float(values.get("longitude"))
    living_area_sqm = _coerce_float(values.get("living_area_sqm"))
    price = _coerce_price(values.get("price"))

    coerced = {
        "latitude": latitude,
        "longitude": longitude,
        "living_area_sqm": living_area_sqm,
        "price": price,
    }
    missing = [f for f in REQUIRED_NORMALIZED_FIELDS if coerced[f] is None]
    if missing:
        raise ParserError(
            f"{cfg.slug} ({url}): missing/unparseable required field(s) {missing} "
            f"after extraction — raw values were {({k: values.get(k) for k in missing})}"
        )

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ParserError(f"{cfg.slug} ({url}): coordinates out of range ({latitude}, {longitude})")
    if living_area_sqm <= 0:
        raise ParserError(f"{cfg.slug} ({url}): non-positive living_area_sqm ({living_area_sqm})")
    if price <= 0:
        raise ParserError(f"{cfg.slug} ({url}): non-positive price ({price})")

    raw_blob = values.get("_raw")
    return ScrapedListing(
        site_name=cfg.slug,
        title=str(values.get("title") or "Untitled listing").strip(),
        description=(str(values["description"]).strip() if values.get("description") else None),
        price=price,
        currency=str(values.get("currency") or cfg.currency_default),
        living_area_sqm=living_area_sqm,
        latitude=latitude,
        longitude=longitude,
        district=(str(values["district"]).strip() if values.get("district") else None),
        agency_name=str(values.get("agency_name") or cfg.agency_name_default or cfg.display_name),
        original_listing_url=url,
        agent_name=(str(values["agent_name"]).strip() if values.get("agent_name") else None),
        agent_phone=(str(values["agent_phone"]).strip() if values.get("agent_phone") else None),
        agent_email=(str(values["agent_email"]).strip() if values.get("agent_email") else None),
        raw_next_data_json=json.dumps(raw_blob, default=str) if raw_blob is not None else None,
    )


def _extract_static_table(soup, field_labels: dict[str, str]) -> dict:
    """
    Archetype C fallback when no inline JS map marker is found: scans
    table rows / definition-list pairs / common "spec row" divs for a
    label match and pulls the adjacent value cell.
    """
    values: dict[str, Any] = {}
    candidate_rows = soup.select("tr") + soup.select("dl > div") + soup.select(".spec-row, .property-detail-row, .detail-row")
    for dest, label in field_labels.items():
        for row in candidate_rows:
            text = row.get_text(" ", strip=True)
            if label.lower() in text.lower():
                cells = row.find_all(["td", "dd", "span"])
                if len(cells) >= 2:
                    values[dest] = cells[-1].get_text(strip=True)
                    break
                if text.lower() != label.lower():
                    # single-cell row like "Surface: 180 m²" — take the remainder after the label
                    values[dest] = text.split(":", 1)[-1].strip() if ":" in text else text.replace(label, "").strip()
                    break
    return values


def _apply_detail_selectors(soup, cfg: SiteConfig, values: dict) -> None:
    """
    Archetype C/D sites that render each field in its own uniquely-classed
    element (e.g. TrovaCasa CMS's `<div class="immobileInfo price">`)
    rather than a generic <table>/<dl> — `_extract_static_table` won't
    match that shape. This selects each configured element directly and,
    if a regex is given for that field, extracts just the matched group
    from its text (anchoring on a unit like "m²" instead of grabbing every
    digit in the element, which would misfire on a residence name that
    happens to contain a number). Mutates `values` in place, never
    overwriting a field that's already been set by an earlier extraction
    step (e.g. a map-marker regex match for coordinates).
    """
    for dest, selector in cfg.detail_field_selectors.items():
        if values.get(dest):
            continue
        element = soup.select_one(selector)
        if element is None:
            continue
        text = element.get_text(" ", strip=True)
        pattern = cfg.detail_field_regex.get(dest)
        if pattern:
            match = re.search(pattern, text)
            if match:
                values[dest] = match.group(1)
            # no match -> leave unset; normalize_listing() will reject the
            # listing if this was a required field, rather than passing
            # through the un-extracted raw text as a guess.
        else:
            values[dest] = text


# ----------------------------------------------------------------------------
# DynamicSiteParser
# ----------------------------------------------------------------------------

class DynamicSiteParser(BaseSiteParser):
    """
    Bound to a `SiteConfig` by `make_dynamic_parser_class()`. Do not
    subclass this directly for a specific site — add a `SiteConfig` entry
    to site_configs.py instead; that's the whole point of the matrix.
    """

    config: SiteConfig = None  # bound per-instance-class by the factory below

    def __init__(self, page: Any, engine):
        super().__init__(page, engine)
        if self.config is None:
            raise ParserError(f"No SiteConfig bound for site '{self.site_name}'")
        # Archetype A only: get_listing_urls() already fetches full detail
        # JSON per listing, so fetch_listing_payload() just replays it
        # instead of hitting the network a second time.
        self._prefetched: dict[str, Any] = {}

    # -- routing --------------------------------------------------------------

    def get_listing_urls(self) -> list[str]:
        return {
            Archetype.IMMOTOOLBOX_API: self._urls_immotoolbox,
            Archetype.FRAMEWORK_HYDRATION: self._urls_hydration,
            Archetype.BOUTIQUE_STATIC: self._urls_boutique,
            Archetype.CLASSIFIED_META: self._urls_classified,
        }[self.config.archetype]()

    def fetch_listing_payload(self, url: str) -> Any:
        return {
            Archetype.IMMOTOOLBOX_API: self._payload_immotoolbox,
            Archetype.FRAMEWORK_HYDRATION: self._payload_hydration,
            Archetype.BOUTIQUE_STATIC: self._payload_html,
            Archetype.CLASSIFIED_META: self._payload_html,
        }[self.config.archetype](url)

    def parse_listing(self, payload: Any, url: str) -> ScrapedListing:
        return {
            Archetype.IMMOTOOLBOX_API: self._parse_immotoolbox,
            Archetype.FRAMEWORK_HYDRATION: self._parse_hydration,
            Archetype.BOUTIQUE_STATIC: self._parse_boutique,
            Archetype.CLASSIFIED_META: self._parse_classified,
        }[self.config.archetype](payload, url)

    # -- Archetype A: ImmoToolBox / regional API -------------------------------

    def _urls_immotoolbox(self) -> list[str]:
        cfg = self.config
        response = self.page.request.get(cfg.api_search_url)
        if response.status >= 400:
            raise ParserError(f"{cfg.slug}: search API {cfg.api_search_url} returned HTTP {response.status}")
        payload = response.json()
        items = resolve_path(payload, cfg.api_results_path) if cfg.api_results_path else payload
        if not isinstance(items, list):
            raise ParserError(f"{cfg.slug}: search API response did not resolve to a list at '{cfg.api_results_path}'")

        urls: list[str] = []
        for item in items:
            listing_id = resolve_path(item, cfg.api_id_field)
            if listing_id is None:
                continue
            public_url = cfg.public_url_template.format(domain=cfg.domain, id=listing_id)

            if cfg.api_listing_url_template:
                detail_url = cfg.api_listing_url_template.format(domain=cfg.domain, id=listing_id)
                detail_resp = self.page.request.get(detail_url)
                if detail_resp.status >= 400:
                    logger.warning("[%s] Detail fetch failed for id=%s (HTTP %d), skipping.", cfg.slug, listing_id, detail_resp.status)
                    continue
                self._prefetched[public_url] = detail_resp.json()
            else:
                # Search response already has everything field_map needs.
                self._prefetched[public_url] = item

            urls.append(public_url)
        return urls

    def _payload_immotoolbox(self, url: str) -> Any:
        if url not in self._prefetched:
            raise ParserError(f"{self.config.slug}: no prefetched API payload cached for {url}")
        return self._prefetched[url]

    def _parse_immotoolbox(self, payload: dict, url: str) -> ScrapedListing:
        cfg = self.config
        values = {dest: resolve_path(payload, path) for dest, path in cfg.field_map.items()}
        values["_raw"] = payload
        return normalize_listing(cfg, values, url)

    # -- Archetype B: JS framework hydration -----------------------------------

    def _urls_hydration(self) -> list[str]:
        cfg = self.config
        self.page.goto(cfg.search_url, timeout=30_000, wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle", timeout=15_000)
        payload = self.extract_next_data(cfg.hydration_var)

        items = resolve_path(payload, cfg.listing_list_path)
        if not isinstance(items, list):
            raise ParserError(f"{cfg.slug}: listing_list_path '{cfg.listing_list_path}' did not resolve to a list")

        urls: list[str] = []
        for item in items:
            value = resolve_path(item, cfg.listing_url_field)
            if not value:
                continue
            urls.append(cfg.listing_url_template.format(domain=cfg.domain, value=value))
        return urls

    def _payload_hydration(self, url: str) -> Any:
        cfg = self.config
        self.page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle", timeout=15_000)
        return self.extract_next_data(cfg.hydration_var)

    def _parse_hydration(self, payload: dict, url: str) -> ScrapedListing:
        cfg = self.config
        listing_obj = resolve_path(payload, cfg.detail_hydration_path) if cfg.detail_hydration_path else payload
        if listing_obj is None:
            raise ParserError(f"{cfg.slug}: detail_hydration_path '{cfg.detail_hydration_path}' not found on {url}")
        values = {dest: resolve_path(listing_obj, path) for dest, path in cfg.field_map.items()}
        values["_raw"] = listing_obj
        return normalize_listing(cfg, values, url)

    # -- Archetype C: boutique static / inline JS map ---------------------------

    def _urls_boutique(self) -> list[str]:
        cfg = self.config
        self.page.goto(cfg.listing_index_url, timeout=30_000, wait_until="domcontentloaded")
        return self._collect_links(cfg.listing_link_selector, cfg.domain)

    def _payload_html(self, url: str) -> str:
        self.page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        return self.page.content()

    def _parse_boutique(self, html: str, url: str) -> ScrapedListing:
        cfg = self.config
        soup = self._soup(html)

        latitude = longitude = None
        for pattern in cfg.map_marker_patterns:
            match = re.search(pattern, html)
            if match:
                latitude, longitude = match.group(1), match.group(2)
                break

        values: dict[str, Any] = {}
        if latitude is not None:
            values["latitude"] = latitude
            values["longitude"] = longitude
        else:
            values.update(_extract_static_table(soup, cfg.static_table_field_labels))

        if values.get("latitude") is None:
            raise ParserError(f"{cfg.slug} ({url}): no coordinates found in JS map markers or the static spec table")

        for dest, label in cfg.static_table_field_labels.items():
            if dest not in values:
                extra = _extract_static_table(soup, {dest: label})
                values.update(extra)

        # Sites that render fields in their own uniquely-classed elements
        # (not a generic table) — e.g. TrovaCasa CMS's <div class="immobileInfo ...">.
        _apply_detail_selectors(soup, cfg, values)

        title_tag = soup.find("meta", property="og:title") or soup.find("title")
        values.setdefault(
            "title",
            title_tag.get("content") if title_tag and title_tag.has_attr("content") else (title_tag.get_text(strip=True) if title_tag else None),
        )
        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        values.setdefault("description", desc_tag.get("content") if desc_tag else None)
        values.setdefault("agency_name", cfg.agency_name_default or cfg.display_name)
        values["_raw"] = None

        return normalize_listing(cfg, values, url)

    # -- Archetype D: classified portal / meta tags ------------------------------

    def _urls_classified(self) -> list[str]:
        cfg = self.config
        self.page.goto(cfg.search_url, timeout=30_000, wait_until="domcontentloaded")
        self._classified_delay()
        return self._collect_links(cfg.listing_link_selector, cfg.domain)

    def _parse_classified(self, html: str, url: str) -> ScrapedListing:
        cfg = self.config
        soup = self._soup(html)

        values: dict[str, Any] = {}
        for dest, meta_name in cfg.meta_tag_map.items():
            tag = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
            if tag and tag.has_attr("content"):
                values[dest] = tag["content"]

        if cfg.price_selector:
            price_el = soup.select_one(cfg.price_selector)
            if price_el:
                values["price"] = price_el.get_text(strip=True)
        if values.get("price") and cfg.price_regex:
            match = re.search(cfg.price_regex, str(values["price"]))
            if match:
                values["price"] = match.group(1)

        # This portal archetype rarely exposes living area via meta tags;
        # fall back to the same static-table scan Archetype C uses, keyed
        # off whatever labels the config supplies (if any).
        if cfg.static_table_field_labels:
            values.update({k: v for k, v in _extract_static_table(soup, cfg.static_table_field_labels).items() if k not in values})

        _apply_detail_selectors(soup, cfg, values)

        values.setdefault("agency_name", cfg.agency_name_default or cfg.display_name)
        values["_raw"] = None

        return normalize_listing(cfg, values, url)

    # -- shared internals -------------------------------------------------------

    def _soup(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml")

    def _collect_links(self, selector: str, domain: str) -> list[str]:
        elements = self.page.query_selector_all(selector)
        urls: list[str] = []
        seen: set[str] = set()
        for el in elements:
            href = el.get_attribute("href")
            if not href:
                continue
            if href.startswith("http"):
                full = href
            elif href.startswith("/"):
                full = f"https://{domain}{href}"
            else:
                full = f"https://{domain}/{href}"
            if full not in seen:
                seen.add(full)
                urls.append(full)
        return urls

    def _classified_delay(self) -> None:
        import random
        lo, hi = self.config.request_delay_range
        self.page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


def make_dynamic_parser_class(config: SiteConfig) -> type[DynamicSiteParser]:
    """
    Builds a concrete DynamicSiteParser subclass bound to one SiteConfig,
    e.g. for config.slug == "barnes_international" this is equivalent to
    writing:

        class DynamicBarnesInternationalParser(DynamicSiteParser):
            site_name = "barnes_international"
            config = <the SiteConfig>

    without hand-writing that class for every one of the 100 sites.
    """
    class_name = "Dynamic" + "".join(part.title() for part in config.slug.split("_")) + "Parser"
    return type(class_name, (DynamicSiteParser,), {"site_name": config.slug, "config": config})
