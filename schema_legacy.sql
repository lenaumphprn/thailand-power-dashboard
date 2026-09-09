-- Thailand Power & Renewables Pulse: minimal schema
CREATE TABLE metric_definition (
  metric_id TEXT PRIMARY KEY, metric_name TEXT NOT NULL, definition TEXT NOT NULL,
  unit TEXT, scope_version TEXT NOT NULL, source_id_primary TEXT, active BOOLEAN DEFAULT TRUE
);
CREATE TABLE source_registry (
  source_id TEXT PRIMARY KEY, organization TEXT, source_name TEXT, url TEXT, tier TEXT,
  cadence TEXT, method TEXT, last_checked_at TIMESTAMP
);
CREATE TABLE metric_observation (
  observation_id INTEGER PRIMARY KEY AUTOINCREMENT, metric_id TEXT NOT NULL,
  period_start DATE, period_end DATE, value REAL, unit TEXT, source_id TEXT,
  publication_date DATE, ingested_at TIMESTAMP NOT NULL, scope_version TEXT NOT NULL,
  qa_status TEXT NOT NULL, raw_source_ref TEXT,
  FOREIGN KEY(metric_id) REFERENCES metric_definition(metric_id),
  FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
);
CREATE UNIQUE INDEX uq_metric_observation
  ON metric_observation(metric_id, period_start, period_end, source_id, publication_date, scope_version);
CREATE TABLE policy_event (
  event_id TEXT PRIMARY KEY, topic TEXT, status_before TEXT, status_after TEXT, event_date DATE,
  effective_date DATE, summary TEXT, source_id TEXT, source_url TEXT, is_draft BOOLEAN DEFAULT FALSE
);
CREATE TABLE project_event (
  event_id TEXT PRIMARY KEY, project_id TEXT, company TEXT, project_name TEXT, technology TEXT,
  capacity_mw REAL, status_before TEXT, status_after TEXT, event_date DATE, expected_cod DATE,
  province TEXT, source_url TEXT
);
CREATE TABLE weekly_digest (
  refresh_date DATE, rank INTEGER, category TEXT, headline TEXT, one_fact TEXT, why_it_matters TEXT,
  score INTEGER, source_url TEXT, event_date DATE, PRIMARY KEY(refresh_date, rank)
);
CREATE TABLE refresh_log (
  run_id TEXT PRIMARY KEY, started_at TIMESTAMP, completed_at TIMESTAMP, sources_checked INTEGER,
  new_observations INTEGER, new_events INTEGER, qa_warnings INTEGER, qa_blocks INTEGER,
  published BOOLEAN, notes TEXT
);
