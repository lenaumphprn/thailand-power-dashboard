PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metric_definition (
  metric_id TEXT PRIMARY KEY,
  metric_name TEXT NOT NULL,
  definition TEXT NOT NULL,
  unit TEXT,
  scope_version TEXT NOT NULL,
  source_id_primary TEXT,
  active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS source_registry (
  source_id TEXT PRIMARY KEY,
  organization TEXT,
  source_name TEXT,
  url TEXT,
  tier TEXT,
  cadence TEXT,
  method TEXT,
  last_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS metric_observation (
  observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  metric_id TEXT NOT NULL,
  period_start TEXT,
  period_end TEXT,
  value REAL,
  unit TEXT,
  source_id TEXT,
  publication_date TEXT,
  effective_start TEXT,
  effective_end TEXT,
  ingested_at TEXT NOT NULL,
  scope_version TEXT NOT NULL,
  qa_status TEXT NOT NULL,
  raw_source_ref TEXT,
  note TEXT,
  FOREIGN KEY(metric_id) REFERENCES metric_definition(metric_id),
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_observation
  ON metric_observation(metric_id, period_start, period_end, source_id,
                        COALESCE(publication_date,''), scope_version);

CREATE TABLE IF NOT EXISTS observation_detail (
  observation_id INTEGER NOT NULL,
  detail_key TEXT NOT NULL,
  detail_value REAL,
  detail_text TEXT,
  unit TEXT,
  PRIMARY KEY(observation_id, detail_key),
  FOREIGN KEY(observation_id) REFERENCES metric_observation(observation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tariff_event (
  event_id TEXT PRIMARY KEY,
  metric_id TEXT NOT NULL,
  publication_date TEXT,
  effective_start TEXT,
  effective_end TEXT,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  customer_scope TEXT,
  source_id TEXT,
  source_url TEXT,
  title TEXT,
  supersedes_event_id TEXT,
  ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_event (
  event_id TEXT PRIMARY KEY,
  topic TEXT,
  status_before TEXT,
  status_after TEXT,
  event_date TEXT,
  effective_date TEXT,
  summary TEXT,
  source_id TEXT,
  source_url TEXT,
  is_draft INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_event (
  event_id TEXT PRIMARY KEY,
  project_id TEXT,
  company TEXT,
  project_name TEXT,
  technology TEXT,
  capacity_mw REAL,
  status_before TEXT,
  status_after TEXT,
  event_date TEXT,
  expected_cod TEXT,
  province TEXT,
  source_url TEXT
);

CREATE TABLE IF NOT EXISTS weekly_digest (
  refresh_date TEXT,
  rank INTEGER,
  category TEXT,
  headline TEXT,
  one_fact TEXT,
  why_it_matters TEXT,
  score INTEGER,
  source_url TEXT,
  event_date TEXT,
  PRIMARY KEY(refresh_date, rank)
);

CREATE TABLE IF NOT EXISTS raw_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  source_url TEXT NOT NULL,
  content_type TEXT,
  sha256 TEXT,
  local_path TEXT,
  status_code INTEGER,
  note TEXT
);

CREATE TABLE IF NOT EXISTS refresh_log (
  run_id TEXT PRIMARY KEY,
  started_at TEXT,
  completed_at TEXT,
  mode TEXT,
  sources_checked INTEGER,
  new_observations INTEGER,
  new_events INTEGER,
  qa_warnings INTEGER,
  qa_blocks INTEGER,
  published INTEGER,
  notes TEXT
);

-- Renewable capacity and project pipeline layer (v0.2)
CREATE TABLE IF NOT EXISTS renewable_capacity_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  as_of_date TEXT NOT NULL,
  scope TEXT NOT NULL,
  status_stage TEXT,
  technology TEXT NOT NULL,
  project_count INTEGER,
  capacity_mw REAL NOT NULL,
  capacity_basis TEXT NOT NULL CHECK(capacity_basis IN ('contracted_sale_mw','installed_mw','target_mw')),
  source_id TEXT,
  source_url TEXT,
  publication_date TEXT,
  qa_status TEXT NOT NULL DEFAULT 'pass',
  note TEXT,
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);
CREATE INDEX IF NOT EXISTS idx_re_capacity_asof ON renewable_capacity_snapshot(as_of_date,scope,status_stage,technology);

CREATE TABLE IF NOT EXISTS procurement_cohort (
  cohort_id TEXT PRIMARY KEY,
  cohort_name TEXT NOT NULL,
  scheme TEXT,
  as_of_date TEXT,
  target_mw REAL,
  selected_projects INTEGER,
  selected_mw REAL,
  capacity_basis TEXT NOT NULL DEFAULT 'contracted_sale_mw',
  status TEXT,
  scod_start_year INTEGER,
  scod_end_year INTEGER,
  source_id TEXT,
  source_url TEXT,
  qa_status TEXT NOT NULL DEFAULT 'pass',
  note TEXT,
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);

CREATE TABLE IF NOT EXISTS procurement_stage_snapshot (
  cohort_id TEXT NOT NULL,
  as_of_date TEXT NOT NULL,
  technology TEXT NOT NULL DEFAULT 'Total',
  stage TEXT NOT NULL CHECK(stage IN ('selected','ppa','cop','licensed','cod')),
  project_count INTEGER,
  capacity_mw REAL NOT NULL,
  capacity_basis TEXT NOT NULL DEFAULT 'contracted_sale_mw',
  source_id TEXT,
  qa_status TEXT NOT NULL DEFAULT 'pass',
  note TEXT,
  PRIMARY KEY(cohort_id,as_of_date,technology,stage),
  FOREIGN KEY(cohort_id) REFERENCES procurement_cohort(cohort_id),
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);

CREATE TABLE IF NOT EXISTS procurement_scod_schedule (
  cohort_id TEXT NOT NULL,
  technology TEXT NOT NULL,
  scod_year INTEGER NOT NULL,
  capacity_mw REAL NOT NULL,
  source_id TEXT,
  qa_status TEXT NOT NULL DEFAULT 'pass',
  note TEXT,
  PRIMARY KEY(cohort_id,technology,scod_year),
  FOREIGN KEY(cohort_id) REFERENCES procurement_cohort(cohort_id),
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);

CREATE TABLE IF NOT EXISTS project_registry (
  project_id TEXT PRIMARY KEY,
  company TEXT,
  project_name TEXT NOT NULL,
  is_aggregate INTEGER NOT NULL DEFAULT 0,
  project_count INTEGER DEFAULT 1,
  technology TEXT,
  province TEXT,
  contracted_mw REAL,
  installed_mw REAL,
  status TEXT,
  status_date TEXT,
  expected_cod_start TEXT,
  expected_cod_end TEXT,
  cohort_id TEXT,
  overlap_group TEXT,
  source_id TEXT,
  source_url TEXT,
  qa_status TEXT NOT NULL DEFAULT 'pass',
  note TEXT,
  FOREIGN KEY(cohort_id) REFERENCES procurement_cohort(cohort_id),
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);
CREATE INDEX IF NOT EXISTS idx_project_status ON project_registry(status,expected_cod_start,company);

-- Self-updating market intelligence layer (v0.3)
CREATE TABLE IF NOT EXISTS market_event (
  event_id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  source_id TEXT,
  source_name TEXT,
  source_tier TEXT,
  source_url TEXT NOT NULL,
  title TEXT NOT NULL,
  body_excerpt TEXT,
  event_date TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  category TEXT NOT NULL,
  topic TEXT,
  company TEXT,
  technology TEXT,
  capacity_mw REAL,
  capacity_mentions_json TEXT,
  status_after TEXT,
  matched_project_id TEXT,
  materiality_score INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0.5,
  is_relevant INTEGER NOT NULL DEFAULT 1,
  review_status TEXT NOT NULL DEFAULT 'auto',
  note TEXT,
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id),
  FOREIGN KEY(matched_project_id) REFERENCES project_registry(project_id)
);
CREATE INDEX IF NOT EXISTS idx_market_event_date ON market_event(event_date DESC, materiality_score DESC);
CREATE INDEX IF NOT EXISTS idx_market_event_topic ON market_event(topic, category, company);

CREATE TABLE IF NOT EXISTS project_alias (
  alias TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  company TEXT,
  match_type TEXT NOT NULL DEFAULT 'contains',
  FOREIGN KEY(project_id) REFERENCES project_registry(project_id)
);

CREATE TABLE IF NOT EXISTS source_watch_state (
  source_id TEXT PRIMARY KEY,
  last_success_at TEXT,
  last_item_date TEXT,
  last_item_url TEXT,
  last_hash TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);
