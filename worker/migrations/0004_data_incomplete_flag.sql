-- Visual flag for listings with no real size/photo data available from
-- their original source (as opposed to just not-yet-entered) — the CRM UI
-- shows these distinctly instead of presenting them as normal complete
-- listings.
ALTER TABLE properties ADD COLUMN data_incomplete INTEGER DEFAULT 0;
