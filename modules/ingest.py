"""Ingestion: upload CV/transcript/base summary/SOP, extract text, store."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
from sqlalchemy.orm import Session

from db.models import Asset
from modules import config_loader

KINDS = ("cv", "transcript", "summary", "sop",
         "recommendation", "english_test", "degree_certificate",
         "research_proposal", "writing_sample", "portfolio", "passport", "photo")
MIN_CHARS = 500  # below this, the PDF is likely scanned -> warn


def extract_text(pdf_path: str | Path) -> str:
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return ""
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts).strip()


def save_upload(session: Session, kind: str, src_path: str | Path,
                original_name: Optional[str] = None) -> Asset:
    """Copy an uploaded file into data/uploads, extract text, persist an Asset."""
    if kind not in KINDS:
        raise ValueError(f"Unknown asset kind: {kind!r}")

    uploads = config_loader.abspath("uploads")
    uploads.mkdir(parents=True, exist_ok=True)
    fname = original_name or Path(src_path).name
    dest = uploads / f"{kind}_{fname}"
    shutil.copyfile(src_path, dest)

    suffix = dest.suffix.lower()
    if suffix == ".pdf":
        text = extract_text(dest)
    elif suffix in (".txt", ".md"):
        text = dest.read_text(encoding="utf-8", errors="ignore")
    else:
        text = ""  # binary docs (images, .docx) — keep the file, don't extract text
    warning = None
    if suffix == ".pdf" and len(text) < MIN_CHARS:
        warning = (
            f"Extracted only {len(text)} characters (<{MIN_CHARS}). "
            "The PDF may be scanned/image-based; OCR may be required."
        )

    # One asset per kind: replace any prior record of the same kind.
    existing = session.query(Asset).filter_by(kind=kind).all()
    for old in existing:
        session.delete(old)

    asset = Asset(
        kind=kind,
        file_path=str(dest),
        extracted_text=text,
        char_count=len(text),
        warning=warning,
    )
    session.add(asset)
    session.flush()
    return asset


def get_asset(session: Session, kind: str) -> Optional[Asset]:
    return session.query(Asset).filter_by(kind=kind).order_by(Asset.created_at.desc()).first()


def asset_paths(session: Session) -> dict[str, str]:
    """Map of kind -> file_path for currently uploaded assets."""
    return {a.kind: a.file_path for a in session.query(Asset).all()}
