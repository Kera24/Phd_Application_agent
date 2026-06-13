-- ScholarReach Phase 3 — reply detection + follow-ups -- Supabase/PostgreSQL
-- Incremental migration on top of 001_init.sql. Apply via the Supabase SQL
-- editor or:  psql $DATABASE_URL -f migrations/002_phase3.sql
-- Idempotent (IF NOT EXISTS). The SQLite dev DB is upgraded in code by
-- db.session._ensure_phase3_columns at startup.

ALTER TABLE emails ADD COLUMN IF NOT EXISTS reply_received_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS gmail_thread_id VARCHAR(128);
ALTER TABLE emails ADD COLUMN IF NOT EXISTS is_followup BOOLEAN DEFAULT FALSE;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS parent_email_id INTEGER REFERENCES emails (id);

ALTER TABLE followups ADD COLUMN IF NOT EXISTS followup_email_id INTEGER REFERENCES emails (id);
ALTER TABLE followups ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE;
