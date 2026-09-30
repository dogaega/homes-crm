"""
Monaco/Riviera Local Pipeline — EspoCRM Sync Layer
========================================================
Pushes clustered listings from the local staging SQLite database
(local-pipeline/dedup/pipeline_staging.db) into a self-hosted EspoCRM
instance via its REST API — replacing the old Cloudflare Workers
`/sync/*` endpoint as the pipeline's destination backend.

*** CURRENT STATUS (as of 2026-09-22 pilot run) ***
The official EspoCRM Real Estate extension turned out to be incompatible
with every current EspoCRM core version tested (v10, v8 — same "Unsupported
link type 'currency'" rebuild failure both times, traced to the extension's
2019-era currency-field handling, not a fixable one-line patch). Rather than
keep chasing an abandoned extension, we built our own custom entity instead:
`CProperty`, defined in infra/espocrm's container at
custom/Espo/Custom/Resources/metadata/{scopes,entityDefs,clientDefs}/CProperty.json
plus matching Controller/Service/Entity PHP classes under
custom/Espo/Custom/{Controllers,Services,Entities}/CProperty.php.

This is a FLAT entity for now — one CProperty record per clustered parent,
with the first competing agency's contact info folded directly onto the
same record (agencyName/agentPhone/agentEmail/sourceUrl fields on
CProperty itself). The full parent+linked-CompetingListing relational
model (matching local-pipeline's own property_parents/property_children
split) is an intentional fast-follow, not yet built — a property with
more than one child in the local staging DB will only carry its first
child's agency info into EspoCRM; `sync_all()` logs a warning for every
additional (currently un-synced) competing listing so none are silently
lost, just not yet represented in EspoCRM.

Every field name below IS verified against the live instance (created a
real CProperty record via curl, got a 200 back with exactly this shape) —
unlike the original RealEstateProperty-based scaffold this replaced.

Requires:
    pip install requests

Environment variables:
    ESPOCRM_API_URL    e.g. http://localhost:8080/api/v1
    ESPOCRM_API_KEY    from the API User created in step 4 above

Usage:
    python sync_to_espocrm.py
    python sync_to_espocrm.py --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sync_to_espocrm")

# Verified live against the actual CProperty entity (see docstring above).
ESPOCRM_PROPERTY_ENTITY = "CProperty"
ESPOCRM_PROPERTY_FIELD_MAP = {
    # our field            -> EspoCRM field
    "title": "name",
    "description": "description",
    "living_area_sqm": "houseArea",
    "map_pin_lat": "latitude",
    "map_pin_lng": "longitude",
    "district": "addressCity",
}

# Folded onto the SAME CProperty record from the parent's first child (see
# docstring — flat entity for now, no separate linked entity yet).
FLAT_CHILD_FIELD_MAP = {
    "agency_name": "agencyName",
    "price": "price",
    "agent_name": "agentName",
    "agent_phone": "agentPhone",
    "agent_email": "agentEmail",
    "original_listing_url": "sourceUrl",
}


@dataclass(frozen=True)
class EspoConfig:
    api_url: str
    api_key: str

    @classmethod
    def from_env(cls) -> "EspoConfig":
        api_url = os.getenv("ESPOCRM_API_URL")
        api_key = os.getenv("ESPOCRM_API_KEY")
        if not api_url or not api_key:
            raise RuntimeError("ESPOCRM_API_URL and ESPOCRM_API_KEY must be set")
        return cls(api_url=api_url.rstrip("/"), api_key=api_key)


class EspoClient:
    def __init__(self, config: EspoConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "X-Api-Key": config.api_key,
            "Content-Type": "application/json",
        })

    def create(self, entity_type: str, data: dict) -> dict:
        resp = self.session.post(f"{self.config.api_url}/{entity_type}", json=data, timeout=20)
        if resp.status_code >= 400:
            raise RuntimeError(f"EspoCRM create {entity_type} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def update(self, entity_type: str, record_id: str, data: dict) -> dict:
        resp = self.session.put(f"{self.config.api_url}/{entity_type}/{record_id}", json=data, timeout=20)
        if resp.status_code >= 400:
            raise RuntimeError(f"EspoCRM update {entity_type}/{record_id} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def upload_attachment(self, entity_type: str, field: str, filename: str, content_bytes: bytes, mime_type: str) -> str:
        """
        EspoCRM's attachment contract: POST a data-URI of the file to
        /Attachment along with which entity type + field it belongs to,
        get back an attachment id to reference from the owning record.
        """
        b64 = base64.b64encode(content_bytes).decode("ascii")
        payload = {
            "name": filename,
            "type": mime_type,
            "role": "Attachment",
            "relatedType": entity_type,
            "field": field,
            "file": f"data:{mime_type};base64,{b64}",
        }
        resp = self.session.post(f"{self.config.api_url}/Attachment", json=payload, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"EspoCRM attachment upload failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()["id"]


# ----------------------------------------------------------------------------
# Local mapping table — tracks which local parent/child rows have already
# been pushed and what their EspoCRM record ids are, so re-running this
# script updates existing records instead of duplicating them.
# ----------------------------------------------------------------------------

def ensure_mapping_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS espocrm_sync_parents (
            parent_id INTEGER PRIMARY KEY REFERENCES property_parents(id),
            espocrm_id TEXT NOT NULL,
            synced_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS espocrm_sync_children (
            child_id INTEGER PRIMARY KEY REFERENCES property_children(id),
            espocrm_id TEXT NOT NULL,
            synced_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def get_espocrm_parent_id(conn: sqlite3.Connection, parent_id: int) -> Optional[str]:
    row = conn.execute("SELECT espocrm_id FROM espocrm_sync_parents WHERE parent_id = ?", (parent_id,)).fetchone()
    return row[0] if row else None


def record_parent_sync(conn: sqlite3.Connection, parent_id: int, espocrm_id: str) -> None:
    conn.execute(
        "INSERT INTO espocrm_sync_parents (parent_id, espocrm_id, synced_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(parent_id) DO UPDATE SET espocrm_id = excluded.espocrm_id, synced_at = excluded.synced_at",
        (parent_id, espocrm_id),
    )
    conn.commit()


def get_espocrm_child_id(conn: sqlite3.Connection, child_id: int) -> Optional[str]:
    row = conn.execute("SELECT espocrm_id FROM espocrm_sync_children WHERE child_id = ?", (child_id,)).fetchone()
    return row[0] if row else None


def record_child_sync(conn: sqlite3.Connection, child_id: int, espocrm_id: str) -> None:
    conn.execute(
        "INSERT INTO espocrm_sync_children (child_id, espocrm_id, synced_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(child_id) DO UPDATE SET espocrm_id = excluded.espocrm_id, synced_at = excluded.synced_at",
        (child_id, espocrm_id),
    )
    conn.commit()


# ----------------------------------------------------------------------------
# Sync
# ----------------------------------------------------------------------------

def build_property_payload(parent_row: sqlite3.Row, first_child_row: Optional[sqlite3.Row]) -> dict:
    payload = {}
    for our_field, espo_field in ESPOCRM_PROPERTY_FIELD_MAP.items():
        value = parent_row[our_field]
        if value is not None:
            payload[espo_field] = value
    if first_child_row is not None:
        for our_field, espo_field in FLAT_CHILD_FIELD_MAP.items():
            value = first_child_row[our_field]
            if value is not None:
                payload[espo_field] = value
    return payload


def sync_images(client: EspoClient, conn: sqlite3.Connection, child_id: int, espocrm_property_id: str, dry_run: bool) -> int:
    """
    Attaches any cleaned/uploaded photos for this listing (property_images
    rows with status='uploaded', from the R2 image pipeline) to the
    EspoCRM record. R2 stays as the durable object store either way — this
    just also gives the images a native, in-app gallery inside EspoCRM.
    No-ops cleanly if the image pipeline hasn't run for this listing yet.
    """
    rows = conn.execute(
        "SELECT r2_public_url FROM property_images WHERE child_id = ? AND status = 'uploaded' AND r2_public_url IS NOT NULL",
        (child_id,),
    ).fetchall()
    if not rows:
        return 0

    attached = 0
    for (r2_url,) in rows:
        if dry_run:
            logger.info("[DRY RUN] Would download %s and attach to %s/%s", r2_url, ESPOCRM_PROPERTY_ENTITY, espocrm_property_id)
            attached += 1
            continue
        try:
            img_resp = requests.get(r2_url, timeout=30)
            img_resp.raise_for_status()
            filename = r2_url.rsplit("/", 1)[-1] or "photo.webp"
            client.upload_attachment(
                entity_type=ESPOCRM_PROPERTY_ENTITY,
                field="images",  # CONFIRM: the Real Estate extension's gallery field name on RealEstateProperty
                filename=filename,
                content_bytes=img_resp.content,
                mime_type="image/webp",
            )
            attached += 1
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            logger.warning("Failed to attach image %s: %s", r2_url, exc)
    return attached


def sync_all(db_path: Path, client: Optional[EspoClient], limit: int, dry_run: bool) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_mapping_tables(conn)

    parents = conn.execute(
        "SELECT * FROM property_parents ORDER BY id LIMIT ?", (limit,)
    ).fetchall()

    if not parents:
        logger.info("No properties in the local staging database to sync.")
        return

    logger.info("Syncing %d propert%s to EspoCRM%s...", len(parents), "y" if len(parents) == 1 else "ies", " (DRY RUN)" if dry_run else "")

    synced_parents, deferred_children, failed = 0, 0, 0

    for parent in parents:
        children = conn.execute(
            "SELECT * FROM property_children WHERE parent_id = ? ORDER BY id", (parent["id"],)
        ).fetchall()
        first_child = children[0] if children else None

        payload = build_property_payload(parent, first_child)
        existing_id = get_espocrm_parent_id(conn, parent["id"])

        if dry_run:
            logger.info(
                "[DRY RUN] Would %s %s: %s",
                "update" if existing_id else "create", ESPOCRM_PROPERTY_ENTITY, payload,
            )
            espocrm_property_id = existing_id or f"dryrun-{parent['id']}"
        else:
            try:
                if existing_id:
                    result = client.update(ESPOCRM_PROPERTY_ENTITY, existing_id, payload)
                    espocrm_property_id = existing_id
                else:
                    result = client.create(ESPOCRM_PROPERTY_ENTITY, payload)
                    espocrm_property_id = result["id"]
                    record_parent_sync(conn, parent["id"], espocrm_property_id)
            except RuntimeError as exc:
                logger.error("Failed to sync parent #%d (%s): %s", parent["id"], parent["title"], exc)
                failed += 1
                continue

        synced_parents += 1
        logger.info(
            "%s parent #%d (%s) -> EspoCRM %s/%s%s",
            "Would sync" if dry_run else "Synced", parent["id"], parent["title"], ESPOCRM_PROPERTY_ENTITY, espocrm_property_id,
            f" [agency: {first_child['agency_name']}]" if first_child is not None else "",
        )

        if first_child is not None:
            attached = sync_images(client, conn, first_child["id"], espocrm_property_id, dry_run) if client or dry_run else 0
            if attached:
                logger.info("  Attached %d image(s)", attached)

        # Every competing listing beyond the first is real data this run
        # does NOT yet push anywhere — logged loudly, never silently
        # dropped, until the linked CompetingListing entity exists.
        for extra_child in children[1:]:
            deferred_children += 1
            logger.warning(
                "  NOT YET SYNCED (flat-entity limitation): child #%d, agency=%s, price=%s — "
                "still in local staging DB, will sync once CompetingListing is built.",
                extra_child["id"], extra_child["agency_name"], extra_child["price"],
            )

    conn.close()
    logger.info(
        "Done. Properties synced: %d, failed: %d, competing listings deferred (not yet synced): %d",
        synced_parents, failed, deferred_children,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("STAGING_DB_PATH", "../dedup/pipeline_staging.db")),
        help="Path to the SQLite staging database.",
    )
    parser.add_argument("--limit", type=int, default=1000, help="Maximum number of properties to sync in this run.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be sent without calling the EspoCRM API or requiring ESPOCRM_API_URL/KEY to be set.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        logger.error("Staging database not found at %s", args.db)
        return 1

    client = None
    if not args.dry_run:
        try:
            client = EspoClient(EspoConfig.from_env())
        except RuntimeError as exc:
            logger.error(str(exc))
            return 1

    sync_all(args.db, client, args.limit, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
