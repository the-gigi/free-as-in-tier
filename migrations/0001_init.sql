-- One row per weekly (or manual) run.
CREATE TABLE runs (
  id         TEXT PRIMARY KEY,
  trigger    TEXT NOT NULL,          -- 'cron' or 'manual'
  started_at TEXT NOT NULL,
  pages      INTEGER NOT NULL
);

-- One row per page per run: what the check found.
CREATE TABLE checks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  provider   TEXT NOT NULL,
  page_id    TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  hash       TEXT,
  changed    INTEGER NOT NULL DEFAULT 0,
  quotas     INTEGER NOT NULL DEFAULT 0,
  changes    INTEGER NOT NULL DEFAULT 0,
  pr_url     TEXT,
  error      TEXT
);
CREATE INDEX checks_by_run ON checks (run_id);
CREATE INDEX checks_by_page ON checks (page_id, checked_at);

-- The page text behind every extraction, so each change has evidence.
-- These would live in R2, which needs a payment method on file to enable.
CREATE TABLE snapshots (
  page_id    TEXT NOT NULL,
  hash       TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content    TEXT NOT NULL,
  PRIMARY KEY (page_id, hash)
);

-- Every quota ever seen. A changed limit closes the old row and opens a new one,
-- so the table is the history.
CREATE TABLE quotas (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  provider    TEXT NOT NULL,
  page_id     TEXT NOT NULL,
  key         TEXT NOT NULL,          -- normalized service|metric
  service     TEXT NOT NULL,
  metric      TEXT NOT NULL,
  limit_value TEXT NOT NULL,
  period      TEXT NOT NULL,
  notes       TEXT NOT NULL DEFAULT '',
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX quotas_active ON quotas (provider, page_id, active);

-- What changed, when. The tracker's PRs and the UI's change log read this.
CREATE TABLE changes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL,
  provider    TEXT NOT NULL,
  page_id     TEXT NOT NULL,
  kind        TEXT NOT NULL,          -- 'added', 'removed' or 'changed'
  service     TEXT NOT NULL,
  metric      TEXT NOT NULL,
  old_value   TEXT,
  new_value   TEXT,
  detected_at TEXT NOT NULL
);
CREATE INDEX changes_by_time ON changes (detected_at);
