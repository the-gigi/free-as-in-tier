-- The tracker no longer opens pull requests: D1 is the record, and the UI and API serve it.
ALTER TABLE checks DROP COLUMN pr_url;
