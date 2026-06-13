-- ScholarReach schema -- Supabase/PostgreSQL
-- Generated from db/models.py by scripts/export_schema.py. Apply via the
-- Supabase SQL editor or:  psql $DATABASE_URL -f migrations/001_init.sql
-- LangGraph checkpoint tables are created separately by PostgresSaver.setup().

CREATE TABLE IF NOT EXISTS assets (
	id SERIAL NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	file_path TEXT NOT NULL, 
	extracted_text TEXT, 
	char_count INTEGER NOT NULL, 
	warning TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS professors (
	id SERIAL NOT NULL, 
	name TEXT NOT NULL, 
	email VARCHAR(320), 
	university TEXT, 
	profile_url TEXT, 
	scholar_url TEXT, 
	research_themes JSON, 
	recent_papers JSON, 
	identified_gap TEXT, 
	proposed_angle TEXT, 
	last_researched_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_professor_email UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS opportunities (
	id SERIAL NOT NULL, 
	source_url TEXT, 
	source_type VARCHAR(32) NOT NULL, 
	opportunity_type VARCHAR(16) NOT NULL, 
	position_title TEXT, 
	university TEXT, 
	country VARCHAR(128), 
	city VARCHAR(128), 
	department TEXT, 
	lab_name TEXT, 
	professor_name TEXT, 
	professor_email VARCHAR(320), 
	professor_profile_url TEXT, 
	deadline DATE, 
	funding_status VARCHAR(16) NOT NULL, 
	funding_evidence TEXT, 
	pipeline_status VARCHAR(32) NOT NULL, 
	required_documents JSON, 
	application_link TEXT, 
	research_fields JSON, 
	eligibility_notes TEXT, 
	international_eligible BOOLEAN, 
	fit_score INTEGER, 
	score_breakdown JSON, 
	raw_text TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	professor_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(professor_id) REFERENCES professors (id)
);

CREATE TABLE IF NOT EXISTS publications (
	id SERIAL NOT NULL, 
	professor_id INTEGER, 
	title TEXT NOT NULL, 
	year INTEGER, 
	venue TEXT, 
	abstract TEXT, 
	source_url TEXT, 
	source_api VARCHAR(32), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(professor_id) REFERENCES professors (id)
);

CREATE TABLE IF NOT EXISTS emails (
	id SERIAL NOT NULL, 
	opportunity_id INTEGER, 
	professor_id INTEGER, 
	subject TEXT, 
	body TEXT, 
	summary_pdf_path TEXT, 
	attachments JSON, 
	quality_gate_passed BOOLEAN NOT NULL, 
	quality_gate_report JSON, 
	status VARCHAR(24) NOT NULL, 
	gmail_draft_id VARCHAR(128), 
	gmail_message_id VARCHAR(128), 
	scheduled_send_at_utc TIMESTAMP WITHOUT TIME ZONE, 
	sent_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	sent_date DATE, 
	followup_due_date DATE, 
	reply_received BOOLEAN NOT NULL, 
	followup_status VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(opportunity_id) REFERENCES opportunities (id), 
	FOREIGN KEY(professor_id) REFERENCES professors (id)
);

CREATE TABLE IF NOT EXISTS research_gaps (
	id SERIAL NOT NULL, 
	opportunity_id INTEGER, 
	professor_id INTEGER, 
	gap TEXT, 
	proposed_angle TEXT, 
	source_publication_ids JSON, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(opportunity_id) REFERENCES opportunities (id), 
	FOREIGN KEY(professor_id) REFERENCES professors (id)
);

CREATE TABLE IF NOT EXISTS approvals (
	id SERIAL NOT NULL, 
	email_id INTEGER, 
	thread_id VARCHAR(128), 
	decision VARCHAR(24) NOT NULL, 
	decided_by VARCHAR(128), 
	edits JSON, 
	reason TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(email_id) REFERENCES emails (id)
);

CREATE TABLE IF NOT EXISTS followups (
	id SERIAL NOT NULL, 
	email_id INTEGER, 
	sent_date DATE, 
	followup_due_date DATE, 
	reply_received BOOLEAN NOT NULL, 
	followup_status VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(email_id) REFERENCES emails (id)
);

CREATE TABLE IF NOT EXISTS pipeline_events (
	id SERIAL NOT NULL, 
	email_id INTEGER, 
	event VARCHAR(64) NOT NULL, 
	detail JSON, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(email_id) REFERENCES emails (id)
);

CREATE TABLE IF NOT EXISTS scheduled_emails (
	id SERIAL NOT NULL, 
	email_id INTEGER, 
	send_at_utc TIMESTAMP WITHOUT TIME ZONE, 
	professor_tz VARCHAR(64), 
	tz_flagged BOOLEAN NOT NULL, 
	job_id VARCHAR(128), 
	status VARCHAR(24) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(email_id) REFERENCES emails (id)
);
