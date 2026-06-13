"""Gmail integration: OAuth, draft creation, gated send.

Non-Negotiable Rules 1 & 4: draft-first by default; sending requires
config.approved_send_mode AND emails.status == 'approved'; dedupe immediately
before send (one professor, one email).
"""
from __future__ import annotations

import base64
import datetime as dt
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Email, Professor
from modules import config_loader, tracker

# Minimal scopes. gmail.send is only requested when send mode is enabled.
COMPOSE_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.compose",
               "https://www.googleapis.com/auth/gmail.send"]


class GmailNotAuthorised(RuntimeError):
    pass


class SendNotPermitted(RuntimeError):
    pass


def _scopes() -> list[str]:
    cfg = config_loader.config()
    return SEND_SCOPES if cfg.get("approved_send_mode") else COMPOSE_SCOPES


def is_authorised() -> bool:
    token = config_loader.abspath("gmail_token")
    return token.exists()


def get_service():
    """Build an authorised Gmail API service from a persisted token."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GmailNotAuthorised("Google API libraries not installed.") from exc

    token_path = config_loader.abspath("gmail_token")
    creds_path = config_loader.abspath("gmail_credentials")
    scopes = _scopes()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise GmailNotAuthorised(
                    f"Missing OAuth client secrets at {creds_path}. "
                    "Download from Google Cloud Console and place it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def build_message_from_fields(to: str, subject: str, body: str,
                              attachment_files: list[str]) -> EmailMessage:
    """Session-free message builder (used by LangChain tools)."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject or ""
    msg.set_content(body or "")
    for path in attachment_files or []:
        if not path or not Path(path).exists():
            continue
        msg.add_attachment(Path(path).read_bytes(), maintype="application",
                           subtype="pdf", filename=Path(path).name)
    return msg


def create_draft_from_fields(to: str, subject: str, body: str,
                             attachment_files: list[str]) -> str:
    """Create a Gmail draft from raw fields. Returns the draft id."""
    service = get_service()
    msg = build_message_from_fields(to, subject, body, attachment_files)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    created = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return created["id"]


def send_from_fields(to: str, subject: str, body: str,
                     attachment_files: list[str], *, max_retries: int = 3) -> str:
    """Send raw fields. HARD GATE: only callable when approved_send_mode is true.

    Callers (the Scheduler node) must additionally verify per-email approval and
    dedupe before invoking this.
    """
    if not config_loader.config().get("approved_send_mode"):
        raise SendNotPermitted("approved_send_mode is false in config.")
    service = get_service()
    msg = build_message_from_fields(to, subject, body, attachment_files)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return sent["id"]
        except Exception as exc:  # pragma: no cover - network
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Send failed after {max_retries} attempts: {last_exc}")


def _build_message(email: Email, prof: Professor,
                   attachment_paths: dict[str, str]) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = prof.email
    msg["Subject"] = email.subject or ""
    msg.set_content(email.body or "")
    for kind in (email.attachments or []):
        path = attachment_paths.get(kind) or (
            email.summary_pdf_path if kind == "summary_pdf" else None
        )
        if not path or not Path(path).exists():
            continue
        data = Path(path).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=Path(path).name)
    return msg


def create_draft(session: Session, email: Email,
                 attachment_paths: dict[str, str]) -> str:
    """Create a Gmail draft. Default, always-permitted path."""
    prof = email.professor
    if not prof or not prof.email:
        raise GmailNotAuthorised("Cannot draft: professor email missing.")
    service = get_service()
    msg = _build_message(email, prof, attachment_paths)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    created = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    email.gmail_draft_id = created["id"]
    tracker.log_event(session, email.id, "gmail_draft_created",
                      {"draft_id": created["id"]})
    session.flush()
    return created["id"]


def _dedupe_ok(session: Session, prof: Professor, this_email_id: int) -> bool:
    """No prior 'sent' email to the same professor."""
    prior = (
        session.query(Email)
        .filter(Email.professor_id == prof.id,
                Email.status == "sent",
                Email.id != this_email_id)
        .first()
    )
    return prior is None


def send(session: Session, email: Email,
         attachment_paths: dict[str, str], *, max_retries: int = 3) -> str:
    """Send an email. Gated: requires approved_send_mode AND status == 'approved'."""
    cfg = config_loader.config()
    if not cfg.get("approved_send_mode"):
        raise SendNotPermitted("approved_send_mode is false in config.")
    if email.status != "approved":
        raise SendNotPermitted(f"Email status is {email.status!r}, must be 'approved'.")
    prof = email.professor
    if not prof or not prof.email:
        raise SendNotPermitted("Professor email missing.")
    if not _dedupe_ok(session, prof, email.id):
        raise SendNotPermitted(
            f"Dedupe block: a prior email to {prof.email} was already sent. "
            "Override requires a logged manual reason."
        )

    service = get_service()
    msg = _build_message(email, prof, attachment_paths)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            sent = service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            email.gmail_message_id = sent["id"]
            email.sent_at = dt.datetime.now(dt.timezone.utc)
            tracker.transition(session, email, "sent", {"message_id": sent["id"]})
            session.flush()
            return sent["id"]
        except Exception as exc:  # pragma: no cover - network
            last_exc = exc
            time.sleep(2 ** attempt)
    tracker.transition(session, email, "failed", {"error": str(last_exc)})
    session.flush()
    raise RuntimeError(f"Send failed after {max_retries} attempts: {last_exc}")
