-- Outreach: who we talk to at each agency, every message in and out, the
-- per-client options we chase, and the queue of messages waiting to go out.

-- People at agencies: pacing and network-growth bookkeeping per person.
ALTER TABLE agency_contacts ADD COLUMN role TEXT;               -- agent / manager / rentals / office
ALTER TABLE agency_contacts ADD COLUMN source TEXT;             -- listing / website / signature / whatsapp / email
ALTER TABLE agency_contacts ADD COLUMN whatsapp_jid TEXT;       -- Baileys jid (number or @lid)
ALTER TABLE agency_contacts ADD COLUMN persona TEXT;            -- how Mark presented himself to them: agent / private
ALTER TABLE agency_contacts ADD COLUMN first_contacted_at TEXT;
ALTER TABLE agency_contacts ADD COLUMN last_contacted_at TEXT;
ALTER TABLE agency_contacts ADD COLUMN last_reply_at TEXT;
ALTER TABLE agency_contacts ADD COLUMN sent_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agency_contacts ADD COLUMN reply_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agency_contacts ADD COLUMN unanswered_streak INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agency_contacts ADD COLUMN paused_until TEXT;
ALTER TABLE agency_contacts ADD COLUMN bad_channels TEXT;       -- JSON: ["email"] after a bounce, etc.
CREATE INDEX IF NOT EXISTS idx_agency_contacts_agency ON agency_contacts(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_contacts_phone ON agency_contacts(phone);
CREATE INDEX IF NOT EXISTS idx_agency_contacts_email ON agency_contacts(email);

-- Every message in or out, on any channel (WhatsApp, email, form, call note).
CREATE TABLE IF NOT EXISTS interactions (
  id TEXT PRIMARY KEY,
  agency_id TEXT REFERENCES agencies(id),
  contact_id TEXT REFERENCES agency_contacts(id),
  channel TEXT NOT NULL,                 -- whatsapp / email / form / call_note
  direction TEXT NOT NULL,               -- in / out
  account TEXT,                          -- mark / third / markmirimsky@gmail.com / propertyone…
  external_id TEXT UNIQUE,               -- WhatsApp message id / Gmail message id
  thread_ref TEXT,                       -- chat jid / Gmail thread id
  peer TEXT,                             -- phone / email / jid of the other side
  at TEXT NOT NULL,
  text TEXT,
  attachments TEXT,                      -- JSON
  property_ids TEXT,                     -- JSON: properties this message is about
  request_ids TEXT,                      -- JSON: client requests it serves
  classification TEXT,                   -- JSON: available / rented / exclusive / needs_file / question…
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interactions_agency ON interactions(agency_id, at);
CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id, at);
CREATE INDEX IF NOT EXISTS idx_interactions_peer ON interactions(peer, at);

-- One row per client request × property we consider or chase.
CREATE TABLE IF NOT EXISTS outreach_options (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,              -- saved_searches.id
  property_id TEXT NOT NULL REFERENCES properties(id),
  origin TEXT NOT NULL,                  -- crm_match / received / manual
  status TEXT NOT NULL DEFAULT 'found',   -- found / contacted / available / rented / no_reply / brochure / sent_to_client / visit / offer / dropped
  mandate_agency_id TEXT REFERENCES agencies(id),
  mandate TEXT,                          -- exclusive / not_exclusive / unknown
  mandate_confirmed_at TEXT,
  available_confirmed_at TEXT,
  received_from TEXT,                    -- who sent it to Mark (for origin = received)
  received_at TEXT,
  last_contacted_at TEXT,
  status_at TEXT NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (request_id, property_id)
);
CREATE INDEX IF NOT EXISTS idx_outreach_options_request ON outreach_options(request_id, status);
CREATE INDEX IF NOT EXISTS idx_outreach_options_property ON outreach_options(property_id);

-- Messages waiting to go out (drafted by AI, approved by Mark or auto), and their fate.
CREATE TABLE IF NOT EXISTS outreach_messages (
  id TEXT PRIMARY KEY,
  agency_id TEXT REFERENCES agencies(id),
  contact_id TEXT REFERENCES agency_contacts(id),
  channel TEXT NOT NULL,                 -- whatsapp / email / form
  account TEXT,
  to_address TEXT,                       -- phone / email / form URL
  subject TEXT,
  body TEXT NOT NULL,
  property_ids TEXT,                     -- JSON
  request_ids TEXT,                      -- JSON
  purpose TEXT,                          -- first_contact / follow_up / pre_visit_check
  status TEXT NOT NULL DEFAULT 'draft',  -- draft / approved / sending / sent / failed / cancelled
  reason TEXT,                           -- why this message (shown to Mark on approval)
  scheduled_at TEXT,
  sent_at TEXT,
  external_id TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outreach_messages_status ON outreach_messages(status, scheduled_at);
