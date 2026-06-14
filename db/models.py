"""SQLAlchemy models for ScholarReach.

Schema mirrors the build spec. SQLite-backed but written to migrate cleanly
to Supabase/Postgres later (no SQLite-only types; JSON stored as TEXT via the
portable JSON type).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


# --- Enumerated string values (kept as plain strings for portability) -------

SOURCE_TYPES = ("web", "linkedin_manual", "job_board", "prospecting")
OPPORTUNITY_TYPES = ("advertised", "speculative")
FUNDING_STATUSES = ("funded", "partial", "unfunded", "self-funded", "unknown")

# Opportunity pipeline status (distinct from email status).
OPP_STATUSES = (
    "new",
    "parsed",
    "needs_email",            # professor email could not be found
    "funding_unknown",        # surfaced for manual decision
    "archived_not_funded",    # terminal: not fully funded
    "researched",
    "scored",
    "prospect",               # speculative candidate awaiting Raj's selection
    "email_ready",
)

# Email state machine.
EMAIL_STATUSES = (
    "draft_created",
    "awaiting_review",
    "approved",
    "scheduled",
    "sent",
    "failed",
    "cancelled",
)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32))
    opportunity_type: Mapped[str] = mapped_column(String(16), default="advertised")

    position_title: Mapped[Optional[str]] = mapped_column(Text)  # nullable for speculative
    university: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(String(128))
    city: Mapped[Optional[str]] = mapped_column(String(128))
    department: Mapped[Optional[str]] = mapped_column(Text)
    lab_name: Mapped[Optional[str]] = mapped_column(Text)

    professor_name: Mapped[Optional[str]] = mapped_column(Text)
    professor_email: Mapped[Optional[str]] = mapped_column(String(320))
    professor_profile_url: Mapped[Optional[str]] = mapped_column(Text)

    deadline: Mapped[Optional[dt.date]] = mapped_column(Date)
    funding_status: Mapped[str] = mapped_column(String(16), default="unknown")
    funding_evidence: Mapped[Optional[str]] = mapped_column(Text)

    pipeline_status: Mapped[str] = mapped_column(String(32), default="new")

    required_documents: Mapped[Optional[list]] = mapped_column(JSON)
    application_link: Mapped[Optional[str]] = mapped_column(Text)
    research_fields: Mapped[Optional[list]] = mapped_column(JSON)
    eligibility_notes: Mapped[Optional[str]] = mapped_column(Text)
    international_eligible: Mapped[Optional[bool]] = mapped_column(Boolean)

    fit_score: Mapped[Optional[int]] = mapped_column(Integer)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)

    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    professor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("professors.id"))
    professor: Mapped[Optional["Professor"]] = relationship(back_populates="opportunities")
    emails: Mapped[list["Email"]] = relationship(back_populates="opportunity")


class Professor(Base):
    __tablename__ = "professors"
    __table_args__ = (UniqueConstraint("email", name="uq_professor_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(String(320))  # unique when present
    university: Mapped[Optional[str]] = mapped_column(Text)
    profile_url: Mapped[Optional[str]] = mapped_column(Text)
    scholar_url: Mapped[Optional[str]] = mapped_column(Text)

    research_themes: Mapped[Optional[list]] = mapped_column(JSON)
    # recent_papers: list of {title, year, venue, url, abstract}
    recent_papers: Mapped[Optional[list]] = mapped_column(JSON)
    identified_gap: Mapped[Optional[str]] = mapped_column(Text)
    proposed_angle: Mapped[Optional[str]] = mapped_column(Text)
    last_researched_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="professor")
    emails: Mapped[list["Email"]] = relationship(back_populates="professor")


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id"))
    professor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("professors.id"))

    subject: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    summary_pdf_path: Mapped[Optional[str]] = mapped_column(Text)
    attachments: Mapped[Optional[list]] = mapped_column(JSON)

    quality_gate_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_gate_report: Mapped[Optional[dict]] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(String(24), default="draft_created")
    gmail_draft_id: Mapped[Optional[str]] = mapped_column(String(128))
    gmail_message_id: Mapped[Optional[str]] = mapped_column(String(128))

    scheduled_send_at_utc: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    sent_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    # Phase-3 follow-up + reply tracking.
    sent_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    followup_due_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    reply_received: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_received_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    followup_status: Mapped[str] = mapped_column(String(16), default="not_needed")
    # Gmail thread id (captured at send) — the key for reply lookups.
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(String(128))
    # Follow-up linkage: a follow-up email points back at its first-contact email.
    is_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id"))

    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="emails")
    professor: Mapped[Optional["Professor"]] = relationship(back_populates="emails")
    events: Mapped[list["PipelineEvent"]] = relationship(back_populates="email")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="email")


class PipelineEvent(Base):
    """Append-only audit log."""

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id"))
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    email: Mapped[Optional["Email"]] = relationship(back_populates="events")


class Followup(Base):
    """Phase 3 — one record per due/generated follow-up.

    `email_id` is the original (first-contact) email; `followup_email_id` is the
    generated follow-up email (null until drafted).
    """

    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id"))
    followup_email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id"))
    sent_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    followup_due_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    reply_received: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class Publication(Base):
    """Verbatim paper records retrieved from a paper API. Rules 3 & 4: every
    cited paper must exist here with its source URL."""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    professor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("professors.id"))
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    venue: Mapped[Optional[str]] = mapped_column(Text)
    abstract: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    source_api: Mapped[Optional[str]] = mapped_column(String(32))  # semantic_scholar|arxiv|dblp
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ResearchGap(Base):
    """Gap + proposed angle, grounded in specific stored publications."""

    __tablename__ = "research_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id"))
    professor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("professors.id"))
    gap: Mapped[Optional[str]] = mapped_column(Text)
    proposed_angle: Mapped[Optional[str]] = mapped_column(Text)
    # IDs of the Publication rows the gap is derived from.
    source_publication_ids: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class Approval(Base):
    """Records each human approval decision made through the interrupt."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id"))
    thread_id: Mapped[Optional[str]] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(24))  # approved|rejected|edited|needs_review
    decided_by: Mapped[Optional[str]] = mapped_column(String(128))
    edits: Mapped[Optional[dict]] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    email: Mapped[Optional["Email"]] = relationship(back_populates="approvals")


class ScheduledEmail(Base):
    """A queued send (APScheduler job mirror) for an approved email."""

    __tablename__ = "scheduled_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id"))
    send_at_utc: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    professor_tz: Mapped[Optional[str]] = mapped_column(String(64))
    tz_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    job_id: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="queued")  # queued|sent|cancelled|failed
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ResearchBrief(Base):
    """Deep-research output for an opportunity: themes, gaps, the chosen gap,
    a research question, a proposed approach, and verified citations (JSON)."""

    __tablename__ = "research_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id"))
    professor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("professors.id"))
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class GeneratedDocument(Base):
    """A generated, editable application document (email | sop | cover | proposal)."""

    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id"))
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow)


class ApplicantProfile(Base):
    """Single-row store of the student's application details.

    Merged OVER config/profile.yaml by config_loader.profile(), so it persists
    across redeploys (Render's disk is ephemeral) while the YAML stays the base.
    `data` holds the editable fields (contact, address, test scores, referees, …);
    research_projects etc. remain in the YAML base.
    """

    __tablename__ = "applicant_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow)


class ApplicationTracking(Base):
    """Per-opportunity application lifecycle status + notes (distinct from the
    email pipeline status). One row per opportunity."""

    __tablename__ = "application_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="interested")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    applied_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow)


class GmailToken(Base):
    """Single-row store of the OAuth token (creds JSON) for hosted Gmail.

    Persists across redeploys (Render's disk is ephemeral); RLS-protected. The
    desktop/local flow still uses the on-disk token file as a fallback.
    """

    __tablename__ = "gmail_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow)


class Asset(Base):
    """Uploaded candidate documents (CV, transcript, base summary, SOP)."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # cv|transcript|summary|sop
    file_path: Mapped[str] = mapped_column(Text)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    warning: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
