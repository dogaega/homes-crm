-- Local scraper pipeline sync: extends the existing property_sources /
-- agencies tables (from 0003_intake_pipeline.sql) so multiple competing
-- agency listings for the same physical property can be pushed from the
-- local machine without ever being rejected or collapsed into one row.
--
-- Design mirrors local-pipeline/db/staging_schema.sql:
--   properties        = parent (one physical unit; identified by
--                        map_lat/map_lng rounded to 5dp + square_feet)
--   property_sources  = child (one row per agency's listing of that unit)

-- One row per agency's listing of a property. Never unique on
-- (property_id) — many rows per property is the whole point. UNIQUE only
-- on source_url, since the same URL re-appearing means "this exact listing
-- was seen again" (an update), not a new competing agency.
CREATE UNIQUE INDEX idx_property_sources_url ON property_sources(source_url)
  WHERE source_url IS NOT NULL;

ALTER TABLE property_sources ADD COLUMN listing_title TEXT;
ALTER TABLE property_sources ADD COLUMN listing_description TEXT;
ALTER TABLE property_sources ADD COLUMN currency TEXT DEFAULT 'EUR';
ALTER TABLE property_sources ADD COLUMN agent_name TEXT;
ALTER TABLE property_sources ADD COLUMN agent_phone TEXT;
ALTER TABLE property_sources ADD COLUMN agent_email TEXT;

-- Per-listing workflow state: every scraped source starts 'active' (shown
-- side-by-side under the parent property). Exactly one flips to 'agreed'
-- once the team confirms the true mandate holder; every sibling silently
-- flips to 'losing' — kept forever for the private log, never deleted.
ALTER TABLE property_sources ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
  CHECK (status IN ('active', 'agreed', 'losing'));

-- Daily 404-check tracking for the continuous sync engine (Task 3 of the
-- brief: 3 consecutive 404s -> auto-archive this specific source).
ALTER TABLE property_sources ADD COLUMN last_checked_at TEXT;
ALTER TABLE property_sources ADD COLUMN consecutive_404_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE property_sources ADD COLUMN is_off_market INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_property_sources_status ON property_sources(status);
CREATE INDEX idx_property_sources_offmarket ON property_sources(is_off_market, consecutive_404_count);

-- properties: coordinate-based identity for the dedup lookup, and a lock
-- pointing at the winning property_sources row once a mandate is agreed.
ALTER TABLE properties ADD COLUMN living_area_sqm REAL;
ALTER TABLE properties ADD COLUMN mandate_source_id TEXT REFERENCES property_sources(id);
ALTER TABLE properties ADD COLUMN pipeline_status TEXT DEFAULT 'uncontacted'
  CHECK (pipeline_status IS NULL OR pipeline_status IN (
    'uncontacted', 'contacted', 'agreed', 'off_market', 'archived'
  ));

-- Lookup used by the sync endpoint's find-or-create: "does a property
-- already exist at this coordinate+area cluster?" Non-unique on purpose —
-- see local-pipeline/dedup/cluster_logic.py for why the constraint must
-- live in application logic, not the schema, so a write is never rejected.
CREATE INDEX idx_properties_spatial ON properties(map_lat, map_lng, living_area_sqm);
