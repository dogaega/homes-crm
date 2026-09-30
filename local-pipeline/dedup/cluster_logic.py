"""
Monaco/Riviera Local Pipeline — Spatial Cluster / De-duplication Engine
=========================================================================
Core rule: NOTHING is ever rejected. Every listing the scraper finds gets
written to `property_children`. Clustering only decides WHICH parent row
that child attaches to — it never blocks, skips, or merges away data.

Identity of a real-world property = (latitude, longitude) rounded to 5
decimal places + living_area_sqm. This is deliberately NOT based on title,
description, or photos, which vary listing-to-listing even for the same
physical unit.

Flow for every scraped listing:
    1. Round lat/lng to 5dp.
    2. Look for an existing property_parents row at that (lat_5dp, lng_5dp,
       living_area_sqm) triple.
       - Found  -> reuse it, do NOT touch its curated title/description/photos.
       - Not found -> create a new parent row from this listing's data.
    3. Always INSERT a new property_children row (or UPDATE it in place if we
       have already seen this exact original_listing_url before — that's an
       update to a known listing, not a new competing agency).
    4. Never delete/merge children. Every agency's price, URL and contact
       stays queryable forever, even after a mandate is agreed elsewhere.

Usage:
    from cluster_logic import ClusterEngine, ScrapedListing

    engine = ClusterEngine(db_path="pipeline_staging.db")
    result = engine.ingest(ScrapedListing(...))
    print(result.parent_id, result.child_id, result.was_new_parent)
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cluster_logic")

COORDINATE_PRECISION = 5  # decimal places, per spec


# ----------------------------------------------------------------------------
# Input / output data shapes
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ScrapedListing:
    """One listing as pulled from a portal's __NEXT_DATA__ block."""
    site_name: str                    # matches sites.name
    title: str
    description: Optional[str]
    price: float
    currency: str
    living_area_sqm: float
    latitude: float
    longitude: float
    district: Optional[str]
    agency_name: str
    original_listing_url: str
    agent_name: Optional[str] = None
    agent_phone: Optional[str] = None
    agent_email: Optional[str] = None
    raw_next_data_json: Optional[str] = None


@dataclass(frozen=True)
class IngestResult:
    parent_id: int
    child_id: int
    was_new_parent: bool
    was_new_child: bool


class ClusterEngineError(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------

class ClusterEngine:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ClusterEngine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- public API ----------------------------------------------------------

    def ingest(self, listing: ScrapedListing) -> IngestResult:
        """
        Ingest one scraped listing. Always succeeds in writing a child row;
        never raises to reject a listing for being a "duplicate" — that
        concept does not exist at the child level, only at the parent
        clustering level.
        """
        self._validate(listing)

        lat_5dp = round(listing.latitude, COORDINATE_PRECISION)
        lng_5dp = round(listing.longitude, COORDINATE_PRECISION)

        with self._conn:  # single transaction: find-or-create parent + upsert child
            site_id = self._get_or_create_site(listing.site_name)

            parent_row = self._find_parent(lat_5dp, lng_5dp, listing.living_area_sqm)
            if parent_row is not None:
                parent_id = parent_row["id"]
                was_new_parent = False
                logger.info(
                    "Matched existing parent #%d at (%.5f, %.5f, %.1fsqm) — "
                    "appending %s as a new/updated child listing.",
                    parent_id, lat_5dp, lng_5dp, listing.living_area_sqm,
                    listing.agency_name,
                )
            else:
                parent_id = self._create_parent(listing, lat_5dp, lng_5dp)
                was_new_parent = True
                logger.info(
                    "No existing cluster at (%.5f, %.5f, %.1fsqm) — created new "
                    "parent #%d.",
                    lat_5dp, lng_5dp, listing.living_area_sqm, parent_id,
                )

            child_id, was_new_child = self._upsert_child(parent_id, site_id, listing)

        return IngestResult(
            parent_id=parent_id,
            child_id=child_id,
            was_new_parent=was_new_parent,
            was_new_child=was_new_child,
        )

    def mark_agreed(self, child_id: int) -> None:
        """
        Team has confirmed the true mandate holder for a listing. Locks the
        parent's public display to this child; every sibling child under the
        same parent silently flips to 'losing' (kept forever, never deleted).
        """
        with self._conn:
            child = self._conn.execute(
                "SELECT id, parent_id FROM property_children WHERE id = ?",
                (child_id,),
            ).fetchone()
            if child is None:
                raise ClusterEngineError(f"No property_children row with id={child_id}")

            parent_id = child["parent_id"]

            self._conn.execute(
                """
                UPDATE property_children
                SET status = CASE WHEN id = ? THEN 'agreed' ELSE 'losing' END
                WHERE parent_id = ?
                """,
                (child_id, parent_id),
            )
            self._conn.execute(
                """
                UPDATE property_parents
                SET status = 'agreed',
                    mandate_agency_id = ?
                WHERE id = ?
                """,
                (child_id, parent_id),
            )
        logger.info(
            "Parent #%d locked to child #%d (agreed). Sibling listings kept as 'losing'.",
            parent_id, child_id,
        )

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _validate(listing: ScrapedListing) -> None:
        if listing.living_area_sqm is None or listing.living_area_sqm <= 0:
            raise ClusterEngineError(
                f"Listing {listing.original_listing_url} has invalid living_area_sqm "
                f"({listing.living_area_sqm}); cannot cluster without it."
            )
        if not (-90 <= listing.latitude <= 90 and -180 <= listing.longitude <= 180):
            raise ClusterEngineError(
                f"Listing {listing.original_listing_url} has invalid coordinates "
                f"({listing.latitude}, {listing.longitude})."
            )

    def _get_or_create_site(self, site_name: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM sites WHERE name = ?", (site_name,)
        ).fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO sites (name, base_url) VALUES (?, ?)",
            (site_name, f"https://{site_name}"),
        )
        return cur.lastrowid

    def _find_parent(
        self, lat_5dp: float, lng_5dp: float, living_area_sqm: float
    ) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT id FROM property_parents
            WHERE lat_5dp = ? AND lng_5dp = ? AND living_area_sqm = ?
            LIMIT 1
            """,
            (lat_5dp, lng_5dp, living_area_sqm),
        ).fetchone()

    def _create_parent(
        self, listing: ScrapedListing, lat_5dp: float, lng_5dp: float
    ) -> int:
        parent_uid = self._make_parent_uid(lat_5dp, lng_5dp, listing.living_area_sqm)
        cur = self._conn.execute(
            """
            INSERT INTO property_parents (
                parent_uid, lat_5dp, lng_5dp, living_area_sqm,
                title, description, district,
                map_pin_lat, map_pin_lng
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parent_uid, lat_5dp, lng_5dp, listing.living_area_sqm,
                listing.title, listing.description, listing.district,
                listing.latitude, listing.longitude,
            ),
        )
        return cur.lastrowid

    def _upsert_child(
        self, parent_id: int, site_id: int, listing: ScrapedListing
    ) -> tuple[int, bool]:
        """
        A child is keyed by original_listing_url (schema UNIQUE constraint).
        Seeing the same URL again = a refresh of that agency's listing
        (price/description change), not a new competing agency — update in
        place. A new URL, even at an identical parent cluster, is always a
        brand-new competing listing and gets its own row.
        """
        existing = self._conn.execute(
            "SELECT id FROM property_children WHERE original_listing_url = ?",
            (listing.original_listing_url,),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()

        if existing:
            child_id = existing["id"]
            self._conn.execute(
                """
                UPDATE property_children
                SET agency_name = ?, agent_name = ?, agent_phone = ?, agent_email = ?,
                    price = ?, currency = ?, raw_next_data_json = ?,
                    consecutive_404_count = 0, is_off_market = 0,
                    last_checked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    listing.agency_name, listing.agent_name, listing.agent_phone,
                    listing.agent_email, listing.price, listing.currency,
                    listing.raw_next_data_json, now, now, child_id,
                ),
            )
            return child_id, False

        cur = self._conn.execute(
            """
            INSERT INTO property_children (
                parent_id, site_id, agency_name, agent_name, agent_phone, agent_email,
                price, currency, original_listing_url, raw_next_data_json,
                last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parent_id, site_id, listing.agency_name, listing.agent_name,
                listing.agent_phone, listing.agent_email, listing.price,
                listing.currency, listing.original_listing_url,
                listing.raw_next_data_json, now,
            ),
        )
        return cur.lastrowid, True

    @staticmethod
    def _make_parent_uid(lat_5dp: float, lng_5dp: float, living_area_sqm: float) -> str:
        raw = f"{lat_5dp:.5f}:{lng_5dp:.5f}:{living_area_sqm:.1f}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------
# CLI smoke test / example
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Cluster engine smoke test")
    parser.add_argument("--db", type=Path, default=Path("pipeline_staging.db"))
    args = parser.parse_args()

    if not args.db.exists():
        print(f"Staging DB not found at {args.db}. Run staging_schema.sql first.", file=sys.stderr)
        sys.exit(1)

    with ClusterEngine(args.db) as engine:
        listing_a = ScrapedListing(
            site_name="dotta_immobilier",
            title="Exceptional 4-room apartment, Monte-Carlo",
            description="Sea view, high ceilings, near the Casino.",
            price=12_500_000,
            currency="EUR",
            living_area_sqm=180.0,
            latitude=43.73862,
            longitude=7.42706,
            district="Monte-Carlo",
            agency_name="Dotta Immobilier",
            original_listing_url="https://dotta.mc/listings/mc-4room-001",
        )
        listing_b = ScrapedListing(
            site_name="engel_volkers_monaco",
            title="Splendide appartement 4 pieces vue mer",
            description="Vue panoramique, proche du Casino de Monte-Carlo.",
            price=12_900_000,
            currency="EUR",
            living_area_sqm=180.0,
            latitude=43.738624,  # differs at 6th decimal only -> rounds to same 5dp cluster
            longitude=7.42706,
            district="Monte-Carlo",
            agency_name="Engel & Volkers Monaco",
            original_listing_url="https://engelvoelkers.com/mc/listings/9981",
        )

        result_a = engine.ingest(listing_a)
        result_b = engine.ingest(listing_b)

        print(f"Listing A -> parent={result_a.parent_id} child={result_a.child_id} "
              f"new_parent={result_a.was_new_parent}")
        print(f"Listing B -> parent={result_b.parent_id} child={result_b.child_id} "
              f"new_parent={result_b.was_new_parent}")
        assert result_a.parent_id == result_b.parent_id, "Expected both listings to cluster to the same parent"
        print("OK: both competing agency listings clustered under one parent, as expected.")
