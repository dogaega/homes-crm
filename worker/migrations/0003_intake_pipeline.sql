-- Property intake pipeline: agencies/contacts sending listings, the source
-- documents they come from (PDF/email/manual), and per-property source
-- records so the same property can arrive from multiple agencies without
-- losing either copy.

CREATE TABLE agencies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  whatsapp TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE agency_contacts (
  id TEXT PRIMARY KEY,
  agency_id TEXT NOT NULL REFERENCES agencies(id),
  name TEXT,
  phone TEXT,
  email TEXT,
  whatsapp TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- One row per raw thing that arrived (a PDF, a forwarded email, a pasted
-- link, a manual paste) before it's been turned into property fields.
CREATE TABLE intake_documents (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL CHECK (source_type IN ('pdf','email','whatsapp','url','manual')),
  raw_text TEXT,
  file_key TEXT,
  source_url TEXT,
  agency_id TEXT REFERENCES agencies(id),
  agency_contact_id TEXT REFERENCES agency_contacts(id),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','extracted','reviewed','rejected')),
  extracted_fields TEXT,
  extraction_confidence TEXT CHECK (extraction_confidence IS NULL OR extraction_confidence IN ('high','medium','low')),
  property_id TEXT REFERENCES properties(id),
  created_by TEXT NOT NULL REFERENCES agents(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Per-property record of every place a listing was sourced from, so the
-- same apartment sent by two agencies doesn't collapse into one lossy row.
CREATE TABLE property_sources (
  id TEXT PRIMARY KEY,
  property_id TEXT NOT NULL REFERENCES properties(id),
  intake_document_id TEXT REFERENCES intake_documents(id),
  agency_id TEXT REFERENCES agencies(id),
  agency_contact_id TEXT REFERENCES agency_contacts(id),
  source_type TEXT NOT NULL CHECK (source_type IN ('pdf','email','whatsapp','url','manual')),
  source_url TEXT,
  price_at_source REAL,
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_agency_contacts_agency ON agency_contacts(agency_id);
CREATE INDEX idx_intake_documents_status ON intake_documents(status);
CREATE INDEX idx_intake_documents_property ON intake_documents(property_id);
CREATE INDEX idx_property_sources_property ON property_sources(property_id);
