-- Adds fields needed for the public marketing site (villa detail template) and
-- the public/private listing flags. Run with:
--   wrangler d1 execute monaco-riviera-crm --remote --file=migration_public_site_fields.sql

ALTER TABLE properties ADD COLUMN slug TEXT;
ALTER TABLE properties ADD COLUMN plot_size REAL;
ALTER TABLE properties ADD COLUMN terrain_description TEXT;
ALTER TABLE properties ADD COLUMN floor_count INTEGER;
ALTER TABLE properties ADD COLUMN house_history TEXT;
ALTER TABLE properties ADD COLUMN concept_description TEXT;
ALTER TABLE properties ADD COLUMN construction_details TEXT;   -- JSON: {walls, insulation, roof, windows, documentation}
ALTER TABLE properties ADD COLUMN engineering_details TEXT;    -- JSON: {heating, underfloor_heating, ventilation, water_supply, sewage, backup_power}
ALTER TABLE properties ADD COLUMN room_layout TEXT;            -- JSON array of {name, description}
ALTER TABLE properties ADD COLUMN floor_plan_urls TEXT;        -- JSON array of image URLs
ALTER TABLE properties ADD COLUMN gallery_urls TEXT;           -- JSON array of image URLs
ALTER TABLE properties ADD COLUMN video_url TEXT;
ALTER TABLE properties ADD COLUMN map_lat REAL;
ALTER TABLE properties ADD COLUMN map_lng REAL;
ALTER TABLE properties ADD COLUMN featured INTEGER DEFAULT 0;
ALTER TABLE properties ADD COLUMN public_listing INTEGER DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_slug ON properties(slug) WHERE slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_properties_public_listing ON properties(public_listing);
