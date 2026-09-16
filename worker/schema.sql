-- Restored 2026-09-16 from live D1 database (monaco-riviera-crm, e84e3e48-4678-4d09-955f-24bdc6f18cff)
-- via: SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL ORDER BY type DESC, name;
-- (This file was briefly overwritten by a colliding parallel session and has been restored to match
-- what is actually applied to the database — see the exact statements below.)

CREATE TABLE activity_logs (
  id TEXT PRIMARY KEY,
  activity_type TEXT NOT NULL,
  agent_id TEXT NOT NULL REFERENCES agents(id),
  created_at TEXT,
  description TEXT NOT NULL,
  entity_id TEXT,
  entity_type TEXT,
  metadata TEXT
);

CREATE TABLE agents (
  id TEXT PRIMARY KEY,
  user_id TEXT UNIQUE,
  agent_name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT,
  bio TEXT,
  license_number TEXT,
  commission_rate REAL,
  hire_date TEXT,
  manager_id TEXT REFERENCES agents(id),
  performance_metrics TEXT,
  profile_photo_url TEXT,
  social_media TEXT,
  specialties TEXT,
  status TEXT,
  territory TEXT,
  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE client_property_interests (
  id TEXT PRIMARY KEY,
  added_by TEXT REFERENCES agents(id),
  client_id TEXT NOT NULL REFERENCES clients(id),
  created_at TEXT,
  interest_level TEXT,
  notes TEXT,
  property_id TEXT NOT NULL REFERENCES properties(id),
  status TEXT,
  updated_at TEXT
);

CREATE TABLE clients (
  id TEXT PRIMARY KEY,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT,
  address TEXT,
  assigned_agent_id TEXT REFERENCES agents(id),
  budget_range TEXT,
  client_type TEXT,
  preferences TEXT,
  preferred_contact_method TEXT,
  source TEXT,
  status TEXT,
  tags TEXT,
  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE communications (
  id TEXT PRIMARY KEY,
  agent_id TEXT REFERENCES agents(id),
  client_id TEXT REFERENCES clients(id),
  communication_type TEXT,
  completed_at TEXT,
  content TEXT,
  created_at TEXT,
  direction TEXT,
  follow_up_date TEXT,
  follow_up_required INTEGER,
  scheduled_at TEXT,
  status TEXT,
  subject TEXT
);

CREATE TABLE document_signatures (
  id TEXT PRIMARY KEY,
  document_id TEXT REFERENCES documents(id),
  ip_address TEXT,
  signature_data TEXT,
  signed_at TEXT,
  signer_email TEXT,
  signer_name TEXT NOT NULL,
  signer_type TEXT NOT NULL,
  user_agent TEXT
);

CREATE TABLE document_templates (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  description TEXT,
  document_type TEXT NOT NULL,
  is_active INTEGER,
  name TEXT NOT NULL,
  template_content TEXT NOT NULL,
  template_fields TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  agent_id TEXT REFERENCES agents(id),
  client_id TEXT REFERENCES clients(id),
  created_at TEXT,
  created_by TEXT NOT NULL REFERENCES agents(id),
  document_name TEXT NOT NULL,
  document_status TEXT,
  document_type TEXT,
  expiration_date TEXT,
  field_values TEXT,
  file_size INTEGER,
  file_url TEXT NOT NULL,
  finalized_at TEXT,
  mime_type TEXT,
  pdf_url TEXT,
  property_id TEXT REFERENCES properties(id),
  signature_required INTEGER,
  signature_status TEXT,
  tags TEXT,
  template_id TEXT REFERENCES document_templates(id),
  title TEXT,
  updated_at TEXT,
  version INTEGER
);

CREATE TABLE inquiries (
  id TEXT PRIMARY KEY,
  assigned_agent_id TEXT REFERENCES agents(id),
  budget_max REAL,
  budget_min REAL,
  client_id TEXT REFERENCES clients(id),
  contact_preference TEXT,
  created_at TEXT,
  email TEXT NOT NULL,
  follow_up_status TEXT,
  inquirer_name TEXT NOT NULL,
  inquiry_date TEXT,
  inquiry_source TEXT CHECK (inquiry_source IS NULL OR inquiry_source IN ('website','referral','walk_in','social_media','advertisement','cold_call','open_house')),
  inquiry_type TEXT,
  lead_score INTEGER,
  notes TEXT,
  phone TEXT,
  preferred_locations TEXT,
  property_of_interest TEXT REFERENCES properties(id),
  timeline TEXT,
  updated_at TEXT
);

CREATE TABLE properties (
  id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  assigned_agent_id TEXT REFERENCES agents(id),
  bathrooms REAL,
  bedrooms INTEGER,
  city TEXT NOT NULL,
  created_at TEXT,
  created_by TEXT NOT NULL REFERENCES agents(id),
  description TEXT,
  features TEXT,
  listing_date TEXT,
  listing_status TEXT CHECK (listing_status IS NULL OR listing_status IN ('active','pending','sold','withdrawn','expired','coming_soon')),
  lot_size REAL,
  mls_number TEXT,
  photos TEXT,
  price REAL,
  property_id TEXT NOT NULL,
  property_type TEXT,
  sold_date TEXT,
  square_feet REAL,
  state TEXT NOT NULL,
  updated_at TEXT,
  virtual_tour_url TEXT,
  year_built INTEGER,
  zip_code TEXT NOT NULL
);

CREATE TABLE sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE showings (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id),
  client_id TEXT NOT NULL REFERENCES clients(id),
  created_at TEXT,
  duration_minutes INTEGER,
  feedback TEXT,
  follow_up_required INTEGER,
  interest_level TEXT,
  notes TEXT,
  property_id TEXT NOT NULL REFERENCES properties(id),
  showing_date TEXT NOT NULL,
  showing_time TEXT NOT NULL,
  showing_type TEXT,
  status TEXT,
  updated_at TEXT
);

CREATE TABLE task_comments (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id),
  comment TEXT NOT NULL,
  created_at TEXT,
  task_id TEXT NOT NULL REFERENCES tasks(id)
);

CREATE TABLE task_templates (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  created_by TEXT REFERENCES agents(id),
  description TEXT,
  is_active INTEGER,
  name TEXT NOT NULL,
  tasks TEXT NOT NULL,
  updated_at TEXT,
  workflow_type TEXT NOT NULL
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  assigned_to TEXT REFERENCES agents(id),
  comments TEXT,
  completed_at TEXT,
  created_at TEXT,
  created_by TEXT REFERENCES agents(id),
  description TEXT,
  due_date TEXT,
  priority TEXT,
  related_entity_id TEXT,
  related_entity_type TEXT,
  status TEXT,
  task_type TEXT,
  template_id TEXT REFERENCES task_templates(id),
  title TEXT NOT NULL
);

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  full_name TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_activity_logs_agent_id ON activity_logs(agent_id);
CREATE INDEX idx_agents_manager_id ON agents(manager_id);
CREATE INDEX idx_agents_user_id ON agents(user_id);
CREATE INDEX idx_clients_assigned_agent_id ON clients(assigned_agent_id);
CREATE INDEX idx_clients_created_at ON clients(created_at);
CREATE INDEX idx_clients_status ON clients(status);
CREATE INDEX idx_communications_agent_id ON communications(agent_id);
CREATE INDEX idx_communications_client_id ON communications(client_id);
CREATE INDEX idx_cpi_added_by ON client_property_interests(added_by);
CREATE INDEX idx_cpi_client_id ON client_property_interests(client_id);
CREATE INDEX idx_cpi_property_id ON client_property_interests(property_id);
CREATE INDEX idx_document_signatures_document_id ON document_signatures(document_id);
CREATE INDEX idx_documents_agent_id ON documents(agent_id);
CREATE INDEX idx_documents_client_id ON documents(client_id);
CREATE INDEX idx_documents_created_by ON documents(created_by);
CREATE INDEX idx_documents_property_id ON documents(property_id);
CREATE INDEX idx_documents_template_id ON documents(template_id);
CREATE INDEX idx_inquiries_assigned_agent_id ON inquiries(assigned_agent_id);
CREATE INDEX idx_inquiries_client_id ON inquiries(client_id);
CREATE INDEX idx_inquiries_property_of_interest ON inquiries(property_of_interest);
CREATE INDEX idx_properties_assigned_agent_id ON properties(assigned_agent_id);
CREATE INDEX idx_properties_created_by ON properties(created_by);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_showings_agent_id ON showings(agent_id);
CREATE INDEX idx_showings_client_id ON showings(client_id);
CREATE INDEX idx_showings_property_id ON showings(property_id);
CREATE INDEX idx_task_comments_agent_id ON task_comments(agent_id);
CREATE INDEX idx_task_comments_task_id ON task_comments(task_id);
CREATE INDEX idx_task_templates_created_by ON task_templates(created_by);
CREATE INDEX idx_tasks_assigned_to ON tasks(assigned_to);
CREATE INDEX idx_tasks_created_by ON tasks(created_by);
CREATE INDEX idx_tasks_template_id ON tasks(template_id);
