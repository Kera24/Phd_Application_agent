"""Gmail integration: OAuth, draft creation, gated send.

Non-Negotiable Rules 1 & 4: draft-first by default; sending requires
config.approved_send_mode AND emails.status == 'approved'; dedupe immediately
before send (one professor, one email).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Email, Professor
from modules import config_loader, tracker

# Minimal scopes. gmail.send is only requested when send mode is enabled;
# gmail.readonly is added when reply detection is enabled (Phase 3).
READONLY = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.compose",
               "https://www.googleapis.com/auth/gmail.send"]
# Web OAuth requests the full set up front so toggling send/reply modes later
# never needs a re-authorise.
WEB_SCOPES = ["https://www.googleapis.com/auth/gmail.compose",
              "https://www.googleapis.com/auth/gmail.send",
              READONLY]


class GmailNotAuthorised(RuntimeError):
    pass


class SendNotPermitted(RuntimeError):
    pass


def _scopes() -> list[str]:
    cfg = config_loader.config()
    scopes = list(SEND_SCOPES if cfg.get("approved_send_mode") else COMPOSE_SCOPES)
    if cfg.get("reply_detection", {}).get("enabled"):
        scopes.append(READONLY)
    return scopes


# --- token storage: DB first (hosted), on-disk file fallback (local dev) -----

def _load_token_dict() -> Optional[dict]:
    try:
        from db import session as dbsession
        from db.models import GmailToken
        with dbsession.session_scope() as s:
            row = s.get(GmailToken, 1)
            if row and row.data:
                return dict(row.data)
    except Exception:
        pass
    token_path = config_loader.abspath("gmail_token")
    if token_path.exists():
        try:
            return json.loads(token_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_token_dict(data: dict) -> None:
    from db import session as dbsession
    from db.models import GmailToken
    with dbsession.session_scope() as s:
        row = s.get(GmailToken, 1)
        if row is None:
            s.add(GmailToken(id=1, data=data))
        else:
            row.data = data
        s.flush()


def is_authorised() -> bool:
    return _load_token_dict() is not None


def get_service():
    """Build an authorised Gmail API service from the stored token.

    Hosted: token comes from the DB (set by the web OAuth callback). Local dev
    with no DB token falls back to the desktop installed-app flow.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GmailNotAuthorised("Google API libraries not installed.") from exc

    info = _load_token_dict()
    if info:
        creds = Credentials.from_authorized_user_info(info)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _save_token_dict(json.loads(creds.to_json()))
            else:
                raise GmailNotAuthorised("Gmail token is invalid/expired — reconnect Gmail.")
        return build("gmail", "v1", credentials=creds)
    return _get_service_desktop()


def _get_service_desktop():
    """Local-only fallback: desktop OAuth flow (opens a browser on this machine)."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = config_loader.abspath("gmail_token")
    creds_path = config_loader.abspath("gmail_credentials")
    scopes = _scopes()
    if not creds_path.exists():
        raise GmailNotAuthorised(
            "Gmail is not connected. On the hosted app use the Connect Gmail button "
            "(web OAuth); locally, place OAuth client secrets at "
            f"{creds_path} and authorise."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


# --- web OAuth (hosted): redirect flow ---------------------------------------

def _web_client_config() -> dict:
    """OAuth 'web' client config from env vars (preferred) or a secrets file."""
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if cid and csec:
        return {"web": {"client_id": cid, "client_secret": csec,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token"}}
    creds_path = config_loader.abspath("gmail_credentials")
    if creds_path.exists():
        data = json.loads(creds_path.read_text(encoding="utf-8"))
        if "web" in data:
            return data
    raise GmailNotAuthorised(
        "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (a Web OAuth "
        "client from Google Cloud Console) on the backend.")


def _web_flow(redirect_uri: str):
    """Build a web OAuth Flow with PKCE disabled.

    PKCE needs the authorize-step code_verifier carried into the (separate)
    token-exchange request; since this is a confidential client (has a secret),
    we disable PKCE instead of persisting the verifier across requests.
    """
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(_web_client_config(), scopes=WEB_SCOPES,
                                   redirect_uri=redirect_uri,
                                   autogenerate_code_verifier=False)


def build_auth_url(redirect_uri: str) -> tuple[str, str]:
    """Return (consent_url, state) for the user to approve in their own browser."""
    return _web_flow(redirect_uri).authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent")


def complete_auth(code: str, redirect_uri: str) -> bool:
    """Exchange the OAuth code for a token and persist it (DB)."""
    # Google may return scopes in a different order / add openid; relax so the
    # exchange doesn't fail on a benign scope mismatch.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    flow = _web_flow(redirect_uri)
    flow.fetch_token(code=code)
    _save_token_dict(json.loads(flow.credentials.to_json()))
    return True


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


def fetch_thread_replies(thread_id: str) -> list[dict]:
    """Return inbound (not-from-us) messages in a Gmail thread.

    Used by reply detection. Gmail-optional: returns [] if Gmail is unauthorised,
    the libraries are missing, or the API errors — never raises into the caller.
    """
    if not thread_id or not is_authorised():
        return []
    try:
        service = get_service()
        me = service.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=["From", "Date"]).execute()
    except Exception:  # pragma: no cover - network / auth
        return []
    inbound = []
    for msg in thread.get("messages", []):
        headers = {h["name"].lower(): h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
        sender = (headers.get("from", "") or "").lower()
        if me and me in sender:
            continue  # our own outgoing message
        inbound.append({"from": headers.get("from"), "date": headers.get("date"),
                        "message_id": msg.get("id")})
    return inbound


def _dedupe_ok(session: Session, prof: Professor, email: Email) -> bool:
    """Block a *duplicate first-contact* email to the same professor.

    A follow-up (is_followup, parent already sent, no reply yet) is a deliberate
    second email and is allowed — "one professor, one *first-contact* email".
    """
    if email.is_followup:
        parent = session.get(Email, email.parent_email_id) if email.parent_email_id else None
        return bool(parent and parent.status == "sent" and not parent.reply_received)
    prior = (
        session.query(Email)
        .filter(Email.professor_id == prof.id,
                Email.status == "sent",
                Email.is_followup == False,  # noqa: E712 - SQLAlchemy boolean filter
                Email.id != email.id)
        .first()
    )
    return prior is None


def send(session: Session, email: Email,
         attachment_paths: dict[str, str], *, max_retries: int = 3,
         allowed_statuses: tuple[str, ...] = ("approved",)) -> str:
    """Send an email. Gated: requires approved_send_mode AND an allowed status.

    Default allowed status is 'approved' (the interactive path). The durable
    dispatcher passes ('approved', 'scheduled') to deliver a queued email when
    its send time arrives — 'scheduled' is only reachable via Human Approval.
    """
    cfg = config_loader.config()
    if not cfg.get("approved_send_mode"):
        raise SendNotPermitted("approved_send_mode is false in config.")
    if email.status not in allowed_statuses:
        raise SendNotPermitted(
            f"Email status is {email.status!r}, must be one of {allowed_statuses}.")
    prof = email.professor
    if not prof or not prof.email:
        raise SendNotPermitted("Professor email missing.")
    if not _dedupe_ok(session, prof, email):
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
            email.gmail_thread_id = sent.get("threadId")
            now = dt.datetime.now(dt.timezone.utc)
            email.sent_at = now
            # Phase 3: stamp the follow-up clock at send time.
            from modules import followups
            email.sent_date = now.date()
            if not email.is_followup:
                email.followup_due_date = followups.followup_due_date(now.date())
            tracker.transition(session, email, "sent", {"message_id": sent["id"]})
            session.flush()
            return sent["id"]
        except Exception as exc:  # pragma: no cover - network
            last_exc = exc
            time.sleep(2 ** attempt)
    tracker.transition(session, email, "failed", {"error": str(last_exc)})
    session.flush()
    raise RuntimeError(f"Send failed after {max_retries} attempts: {last_exc}")
