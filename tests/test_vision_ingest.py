"""Phase 1 — image/PDF intake via Claude vision.

Vision is mocked (no network / no API key needed). Verifies the module's
guard rails and that the ingest-file endpoint feeds the existing pipeline.
"""
import io

import pytest
from fastapi.testclient import TestClient

from modules import vision_extract


# --- vision_extract unit tests ---------------------------------------------

def test_is_supported():
    assert vision_extract.is_supported("poster.png")
    assert vision_extract.is_supported("flyer.PDF")
    assert vision_extract.is_supported("photo.jpeg")
    assert not vision_extract.is_supported("notes.docx")
    assert not vision_extract.is_supported("data.csv")


def test_content_block_rejects_unsupported():
    with pytest.raises(vision_extract.VisionUnavailable):
        vision_extract._content_block(b"x", ".docx")


def test_transcribe_unsupported_type_raises():
    with pytest.raises(vision_extract.VisionUnavailable):
        vision_extract.transcribe_file(b"x", "notes.docx")


def test_transcribe_without_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(vision_extract.VisionUnavailable):
        vision_extract.transcribe_file(b"\x89PNG", "poster.png")


# --- endpoint integration test (vision mocked) ------------------------------

FUNDED_POSTING = (
    "Fully funded PhD position with full stipend. Professor Jane Doe "
    "(jane.doe@example.edu), Example University, Department of Computer Science. "
    "Topic: medical imaging. Apply at https://example.edu/apply."
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Isolated SQLite for the app's startup; keyless so the pipeline is deterministic.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Mock vision so no network/credits are needed.
    monkeypatch.setattr(vision_extract, "transcribe_file",
                        lambda data, filename: FUNDED_POSTING)
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_ingest_image_runs_pipeline(client):
    files = {"file": ("poster.png", io.BytesIO(b"\x89PNG\r\n fake bytes"), "image/png")}
    r = client.post("/opportunities/ingest-file", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "thread_id" in body
    assert body["status"] in ("awaiting_approval", "completed")
    assert body["extraction"]["method"] == "vision"
    assert body["extraction"]["char_count"] == len(FUNDED_POSTING)


def test_ingest_unsupported_type_rejected(client):
    files = {"file": ("resume.docx", io.BytesIO(b"PK fake"), "application/octet-stream")}
    r = client.post("/opportunities/ingest-file", files=files)
    assert r.status_code == 400
