-- Monaco listings aggregator (local-pipeline/PLAN.md, phase 2).
--
-- Model, extending 0003/0005:
--   properties        = parent: one physical unit, origin = 'pipeline'
--   property_sources  = child: one agency's listing of it (keyed by source_url)
-- Scrapers push batches per site run (POST /sync/listings); the Worker does
-- change detection (events + price_history), duplicate matching (auto-merge
-- or review_queue) and removal only after 3 missed days of *successful* runs.

-- ── geo reference ───────────────────────────────────────────────────────
CREATE TABLE quarters (
  id TEXT PRIMARY KEY,               -- slug, e.g. 'monte-carlo'
  name TEXT NOT NULL,
  name_ru TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',  -- JSON: lowercase spellings seen on sites (CIM q_<Quarter> included)
  centroid_lat REAL,
  centroid_lng REAL,
  centroid_approx INTEGER NOT NULL DEFAULT 1,  -- 1 until replaced by the polygon centroid
  polygon TEXT                       -- GeoJSON, filled in phase 5
);

CREATE TABLE buildings (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,     -- lowercase, no accents/punctuation, 'le/la/les/residence' stripped
  aliases TEXT NOT NULL DEFAULT '[]',
  quarter TEXT REFERENCES quarters(id),
  address TEXT,
  lat REAL,
  lng REAL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_buildings_norm ON buildings(normalized_name);

-- ── agencies: identity of every scraped site ────────────────────────────
ALTER TABLE agencies ADD COLUMN site_key TEXT;          -- stable scraper key, e.g. 'mcre'
ALTER TABLE agencies ADD COLUMN website TEXT;
ALTER TABLE agencies ADD COLUMN cim_slug TEXT;          -- chambre-immobiliere-monaco.mc/fr/agence/<slug>
ALTER TABLE agencies ADD COLUMN manager TEXT;
ALTER TABLE agencies ADD COLUMN address TEXT;
CREATE UNIQUE INDEX idx_agencies_site_key ON agencies(site_key) WHERE site_key IS NOT NULL;
CREATE INDEX idx_agencies_cim_slug ON agencies(cim_slug);

-- ── property_sources: full per-listing detail ───────────────────────────
ALTER TABLE property_sources ADD COLUMN site_key TEXT;
ALTER TABLE property_sources ADD COLUMN external_ref TEXT;          -- agency's own reference
ALTER TABLE property_sources ADD COLUMN transaction_type TEXT
  CHECK (transaction_type IS NULL OR transaction_type IN ('sale','rent'));
ALTER TABLE property_sources ADD COLUMN rent_period TEXT
  CHECK (rent_period IS NULL OR rent_period IN ('month','week','year','season'));
ALTER TABLE property_sources ADD COLUMN price_on_request INTEGER NOT NULL DEFAULT 0;
ALTER TABLE property_sources ADD COLUMN property_type TEXT;         -- apartment, villa, office, shop, parking, cellar, ...
ALTER TABLE property_sources ADD COLUMN bedrooms INTEGER;
ALTER TABLE property_sources ADD COLUMN rooms INTEGER;
ALTER TABLE property_sources ADD COLUMN bathrooms INTEGER;
ALTER TABLE property_sources ADD COLUMN living_area_sqm REAL;
ALTER TABLE property_sources ADD COLUMN terrace_sqm REAL;
ALTER TABLE property_sources ADD COLUMN floor INTEGER;
ALTER TABLE property_sources ADD COLUMN parking INTEGER;            -- number of spaces
ALTER TABLE property_sources ADD COLUMN cellar INTEGER;             -- 0/1
ALTER TABLE property_sources ADD COLUMN sea_view INTEGER;           -- 0/1
ALTER TABLE property_sources ADD COLUMN building_name TEXT;
ALTER TABLE property_sources ADD COLUMN building_id TEXT REFERENCES buildings(id);
ALTER TABLE property_sources ADD COLUMN address TEXT;
ALTER TABLE property_sources ADD COLUMN quarter TEXT;               -- quarters.id
ALTER TABLE property_sources ADD COLUMN lat REAL;
ALTER TABLE property_sources ADD COLUMN lng REAL;
ALTER TABLE property_sources ADD COLUMN coord_source TEXT
  CHECK (coord_source IS NULL OR coord_source IN ('listing','building','quarter'));
ALTER TABLE property_sources ADD COLUMN agent_whatsapp TEXT;
ALTER TABLE property_sources ADD COLUMN agency_phone TEXT;
ALTER TABLE property_sources ADD COLUMN agency_email TEXT;
ALTER TABLE property_sources ADD COLUMN photo_urls TEXT;            -- JSON array, loaded from source on demand
ALTER TABLE property_sources ADD COLUMN hero_image_key TEXT;        -- R2 key (WebP)
ALTER TABLE property_sources ADD COLUMN hero_phash TEXT;            -- 64-bit perceptual hash, hex
ALTER TABLE property_sources ADD COLUMN hero_saved_at TEXT;
ALTER TABLE property_sources ADD COLUMN extra TEXT;                 -- JSON: fields without a column
ALTER TABLE property_sources ADD COLUMN first_seen_at TEXT;
ALTER TABLE property_sources ADD COLUMN last_seen_at TEXT;
ALTER TABLE property_sources ADD COLUMN detail_scraped_at TEXT;
ALTER TABLE property_sources ADD COLUMN last_run_id TEXT;
-- Removal: counted in calendar days on which the site's index crawl
-- succeeded completely and this URL was absent.
ALTER TABLE property_sources ADD COLUMN missed_days INTEGER NOT NULL DEFAULT 0;
ALTER TABLE property_sources ADD COLUMN last_missed_date TEXT;
ALTER TABLE property_sources ADD COLUMN removed_at TEXT;
ALTER TABLE property_sources ADD COLUMN match_tier TEXT;            -- how it joined its parent: new|coords|building|phash|review
CREATE INDEX idx_property_sources_site ON property_sources(site_key, is_off_market);
CREATE INDEX idx_property_sources_agency_ref ON property_sources(agency_id, external_ref);
CREATE INDEX idx_property_sources_phash ON property_sources(hero_phash) WHERE hero_phash IS NOT NULL;

-- ── properties: merged parent ───────────────────────────────────────────
ALTER TABLE properties ADD COLUMN origin TEXT NOT NULL DEFAULT 'crm'
  CHECK (origin IN ('crm','pipeline'));
ALTER TABLE properties ADD COLUMN transaction_type TEXT
  CHECK (transaction_type IS NULL OR transaction_type IN ('sale','rent'));
ALTER TABLE properties ADD COLUMN quarter TEXT;
ALTER TABLE properties ADD COLUMN building_id TEXT REFERENCES buildings(id);
ALTER TABLE properties ADD COLUMN building_name TEXT;
ALTER TABLE properties ADD COLUMN terrace_sqm REAL;
ALTER TABLE properties ADD COLUMN floor INTEGER;
ALTER TABLE properties ADD COLUMN sea_view INTEGER;
ALTER TABLE properties ADD COLUMN coord_source TEXT;
ALTER TABLE properties ADD COLUMN hero_image_key TEXT;
ALTER TABLE properties ADD COLUMN source_count INTEGER NOT NULL DEFAULT 0;   -- active sources
ALTER TABLE properties ADD COLUMN first_seen_at TEXT;
ALTER TABLE properties ADD COLUMN last_seen_at TEXT;
ALTER TABLE properties ADD COLUMN merged_into TEXT REFERENCES properties(id);  -- set when a review merge empties it
CREATE INDEX idx_properties_origin ON properties(origin, transaction_type, quarter);

-- ── history & events ────────────────────────────────────────────────────
CREATE TABLE price_history (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES property_sources(id),
  property_id TEXT NOT NULL REFERENCES properties(id),
  price REAL,
  previous_price REAL,
  currency TEXT,
  observed_at TEXT NOT NULL
);
CREATE INDEX idx_price_history_source ON price_history(source_id, observed_at);
CREATE INDEX idx_price_history_property ON price_history(property_id, observed_at);

CREATE TABLE listing_events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('new','price_drop','price_rise','removed','relisted','off_market','merged')),
  source_id TEXT REFERENCES property_sources(id),
  property_id TEXT REFERENCES properties(id),
  site_key TEXT,
  run_id TEXT,
  data TEXT,                         -- JSON
  created_at TEXT NOT NULL
);
CREATE INDEX idx_listing_events_created ON listing_events(created_at);
CREATE INDEX idx_listing_events_property ON listing_events(property_id);

-- ── runs & site health ──────────────────────────────────────────────────
CREATE TABLE scrape_runs (
  id TEXT PRIMARY KEY,
  site_key TEXT NOT NULL,
  agency_id TEXT REFERENCES agencies(id),
  runner TEXT NOT NULL CHECK (runner IN ('server','local')),
  mode TEXT NOT NULL CHECK (mode IN ('full','light')),
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running','ok','partial','blocked','failed')),
  index_complete INTEGER NOT NULL DEFAULT 0,
  index_count INTEGER,
  detail_count INTEGER,
  new_count INTEGER NOT NULL DEFAULT 0,
  updated_count INTEGER NOT NULL DEFAULT 0,
  price_change_count INTEGER NOT NULL DEFAULT 0,
  removed_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX idx_scrape_runs_site ON scrape_runs(site_key, started_at);

CREATE TABLE site_health (
  site_key TEXT PRIMARY KEY,
  agency_id TEXT REFERENCES agencies(id),
  runner TEXT NOT NULL DEFAULT 'server',
  last_status TEXT,
  last_run_at TEXT,
  last_success_at TEXT,
  consecutive_blocks INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_index_count INTEGER,
  baseline_index_count REAL,         -- EWMA of successful index counts
  flag_broken INTEGER NOT NULL DEFAULT 0,      -- 0 listings or -50% vs baseline
  flag_move_local INTEGER NOT NULL DEFAULT 0,  -- blocked N days in a row on the server
  updated_at TEXT NOT NULL
);

-- ── merging ─────────────────────────────────────────────────────────────
CREATE TABLE review_queue (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES property_sources(id),
  candidate_property_id TEXT NOT NULL REFERENCES properties(id),
  score REAL NOT NULL,
  reasons TEXT NOT NULL,             -- JSON array of matched criteria
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','merged','rejected')),
  decided_by TEXT REFERENCES agents(id),
  decided_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (source_id, candidate_property_id)  -- a rejection is remembered forever
);
CREATE INDEX idx_review_queue_status ON review_queue(status, created_at);

-- ── CRM-side marks & alerts ─────────────────────────────────────────────
CREATE TABLE contact_marks (
  id TEXT PRIMARY KEY,
  property_id TEXT NOT NULL REFERENCES properties(id),
  agency_id TEXT REFERENCES agencies(id),
  source_id TEXT REFERENCES property_sources(id),
  marked_by TEXT REFERENCES agents(id),
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_contact_marks_property ON contact_marks(property_id);

CREATE TABLE saved_searches (
  id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(id),
  agent_id TEXT REFERENCES agents(id),
  name TEXT NOT NULL,
  criteria TEXT NOT NULL,            -- JSON: transaction_type, price_min/max, area_min, bedrooms_min, quarters[], sea_view, ...
  channel TEXT,                      -- telegram|whatsapp|email (TBD)
  active INTEGER NOT NULL DEFAULT 1,
  last_notified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Official Monaco quarters (Ordonnance Souveraine 2013) plus the Mareterra
-- extension. Centroids are approximate (centroid_approx = 1) and only used
-- as the last-resort map position; polygons come in phase 5.
INSERT INTO quarters (id, name, name_ru, aliases, centroid_lat, centroid_lng) VALUES
 ('monaco-ville', 'Monaco-Ville', 'Монако-Вилль', '["monaco-ville","monaco ville","le rocher","rocher","monaco-ville/le rocher"]', 43.7307, 7.4218),
 ('la-condamine', 'La Condamine', 'Ла Кондамин', '["la condamine","condamine","port hercule","port"]', 43.7349, 7.4204),
 ('fontvieille', 'Fontvieille', 'Фонвьей', '["fontvieille"]', 43.7280, 7.4152),
 ('monte-carlo', 'Monte-Carlo', 'Монте-Карло', '["monte-carlo","monte carlo","spelugues","spélugues","carre d''or","carré d''or","golden square","monte-carlo/spelugues"]', 43.7398, 7.4272),
 ('larvotto', 'Larvotto', 'Ларвотто', '["larvotto","bas moulins","larvotto/bas moulins","bas-moulins"]', 43.7445, 7.4330),
 ('la-rousse', 'La Rousse / Saint-Roman', 'Ла Русс / Сен-Роман', '["la rousse","rousse","saint-roman","saint roman","st roman","la rousse/saint roman","la rousse-saint roman"]', 43.7488, 7.4385),
 ('saint-michel', 'Saint-Michel', 'Сен-Мишель', '["saint-michel","saint michel","st michel"]', 43.7405, 7.4235),
 ('moneghetti', 'Les Moneghetti', 'Монегетти', '["moneghetti","les moneghetti","boulevard de belgique","moneghetti/boulevard de belgique","jardin exotique"]', 43.7360, 7.4165),
 ('la-colle', 'La Colle', 'Ла Коль', '["la colle","colle"]', 43.7330, 7.4128),
 ('les-revoires', 'Les Révoires', 'Ле Ревуар', '["les revoires","les révoires","revoires","révoires"]', 43.7385, 7.4200),
 ('mareterra', 'Mareterra', 'Маретерра', '["mareterra","le portier","portier","anse du portier"]', 43.7438, 7.4305);
