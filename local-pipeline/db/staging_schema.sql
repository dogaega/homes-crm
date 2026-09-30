-- ============================================================================
-- Monaco/Riviera Local Pipeline — SQLite Staging Database Schema
-- Parent-Child Property De-duplication Matrix
--
-- This database is local-only (never exposed to the internet). It is the
-- working store for the scraper, dedup engine, and image pipeline before
-- clean structured payloads are pushed to the Cloudflare Workers CRM API.
--
-- Usage:
--   sqlite3 pipeline_staging.db < staging_schema.sql
-- ============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ----------------------------------------------------------------------------
-- sites: the ~30-40 Monaco/Riviera portals being scraped
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,          -- e.g. "dotta_immobilier"
    base_url        TEXT NOT NULL,
    mask_file       TEXT,                          -- /masks/site_name_mask.png
    is_active       INTEGER NOT NULL DEFAULT 1,     -- 0/1
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- property_parents: the canonical, de-duplicated real-world property.
-- Identity is derived from (lat, lng rounded to 5dp) + living_area_sqm,
-- NEVER from title/description text.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_parents (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_uid              TEXT NOT NULL UNIQUE,   -- stable hash, see dedup engine

    -- Spatial identity (the de-duplication key)
    lat_5dp                 REAL NOT NULL,           -- latitude rounded to 5 decimal places
    lng_5dp                 REAL NOT NULL,           -- longitude rounded to 5 decimal places
    living_area_sqm         REAL NOT NULL,

    -- Master/display data (curated from whichever child is richest)
    title                   TEXT NOT NULL,
    description              TEXT,
    district                TEXT,
    photos_json              TEXT NOT NULL DEFAULT '[]',  -- JSON array of R2 object keys (cleaned)
    map_pin_lat              REAL,
    map_pin_lng              REAL,

    -- Pipeline / CRM state
    status                   TEXT NOT NULL DEFAULT 'uncontacted'
                                 CHECK (status IN (
                                     'uncontacted',
                                     'contacted',
                                     'agreed',
                                     'off_market',
                                     'archived'
                                 )),
    mandate_agency_id        INTEGER REFERENCES property_children(id) ON DELETE SET NULL,
    is_public                INTEGER NOT NULL DEFAULT 0,   -- global public toggle (0/1)

    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                TEXT NOT NULL DEFAULT (datetime('now')),
    synced_to_crm_at          TEXT                           -- last successful push to Workers API

    -- NOTE: deliberately NO UNIQUE constraint on (lat_5dp, lng_5dp, living_area_sqm).
    -- A given coordinate+area cluster legitimately has exactly ONE parent row, but
    -- that invariant is enforced by the dedup engine's find-or-create lookup
    -- (see cluster_logic.py), never by the schema. A hard UNIQUE here would raise
    -- on any race/retry and risk rejecting a legitimate write instead of the
    -- intended behavior: every competing listing must always be captured as a
    -- new property_children row against the existing parent, never dropped.
);

-- Non-unique index: used by the dedup engine to look up "does a parent already
-- exist at this coordinate+area cluster?" It speeds up the lookup without ever
-- blocking a write.
CREATE INDEX IF NOT EXISTS idx_parents_spatial
    ON property_parents (lat_5dp, lng_5dp, living_area_sqm);
CREATE INDEX IF NOT EXISTS idx_parents_status
    ON property_parents (status);
CREATE INDEX IF NOT EXISTS idx_parents_sync
    ON property_parents (synced_to_crm_at);

-- ----------------------------------------------------------------------------
-- property_children: one row per (portal listing) bound to a parent.
-- Holds everything that is agency/listing-specific and private.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_children (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id                INTEGER NOT NULL REFERENCES property_parents(id) ON DELETE CASCADE,
    site_id                  INTEGER NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,

    -- Listing-specific data
    agency_name               TEXT NOT NULL,
    agent_name                 TEXT,
    agent_phone                TEXT,
    agent_email                 TEXT,
    price                        REAL NOT NULL,
    currency                     TEXT NOT NULL DEFAULT 'EUR',
    original_listing_url          TEXT NOT NULL UNIQUE,
    raw_next_data_json             TEXT,                 -- verbatim extracted __NEXT_DATA__ blob, for audit/re-parse

    -- Per-listing workflow state. Every child starts 'active' (i.e. one of the
    -- competing offers shown side-by-side under the parent). The team sets
    -- exactly one child to 'agreed' once the true mandate holder is confirmed;
    -- the parent then locks its public display to that child (see
    -- property_parents.mandate_agency_id) while every other child silently
    -- flips to 'losing' and stays in the private log — never deleted.
    status                          TEXT NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active', 'agreed', 'losing')),

    -- Liveness / archival tracking
    last_checked_at                 TEXT,
    consecutive_404_count            INTEGER NOT NULL DEFAULT 0,
    is_off_market                     INTEGER NOT NULL DEFAULT 0,  -- 0/1, set after 3 consecutive 404s

    -- Private CRM workflow notes (kept even after a competing agency wins the mandate)
    private_notes                      TEXT,

    first_seen_at                        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_children_parent
    ON property_children (parent_id);
CREATE INDEX IF NOT EXISTS idx_children_site
    ON property_children (site_id);
CREATE INDEX IF NOT EXISTS idx_children_offmarket
    ON property_children (is_off_market, consecutive_404_count);
CREATE INDEX IF NOT EXISTS idx_children_status
    ON property_children (status);

-- ----------------------------------------------------------------------------
-- property_images: raw scraped photos staged locally before/after inpainting,
-- tracked per child (source) and rolled up into parent.photos_json once clean.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_images (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id               INTEGER NOT NULL REFERENCES property_children(id) ON DELETE CASCADE,

    source_url               TEXT NOT NULL,
    local_raw_path             TEXT,               -- path on local disk before processing
    local_clean_path            TEXT,              -- path to LaMa-inpainted + WebP output
    r2_object_key                 TEXT,             -- final key in the R2 bucket, once uploaded
    r2_public_url                  TEXT,

    width_px                        INTEGER,
    height_px                       INTEGER,
    file_size_bytes                  INTEGER,

    status                            TEXT NOT NULL DEFAULT 'pending'
                                          CHECK (status IN (
                                              'pending',
                                              'downloaded',
                                              'inpainted',
                                              'optimized',
                                              'uploaded',
                                              'failed'
                                          )),
    error_message                      TEXT,

    created_at                          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                          TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE (child_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_images_child
    ON property_images (child_id);
CREATE INDEX IF NOT EXISTS idx_images_status
    ON property_images (status);

-- ----------------------------------------------------------------------------
-- sync_log: audit trail of every push attempt to the Cloudflare Workers API
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id           INTEGER REFERENCES property_parents(id) ON DELETE SET NULL,
    direction             TEXT NOT NULL CHECK (direction IN ('push', 'status_update')),
    request_payload         TEXT,
    response_status           INTEGER,
    response_body              TEXT,
    success                      INTEGER NOT NULL DEFAULT 0,
    created_at                    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sync_log_parent
    ON sync_log (parent_id);

-- ----------------------------------------------------------------------------
-- Triggers: keep updated_at fresh
-- ----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_parents_updated_at
AFTER UPDATE ON property_parents
FOR EACH ROW
BEGIN
    UPDATE property_parents SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_children_updated_at
AFTER UPDATE ON property_children
FOR EACH ROW
BEGIN
    UPDATE property_children SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_images_updated_at
AFTER UPDATE ON property_images
FOR EACH ROW
BEGIN
    UPDATE property_images SET updated_at = datetime('now') WHERE id = NEW.id;
END;
