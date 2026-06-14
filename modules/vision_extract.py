"""Vision intake: read an image or PDF of an opportunity posting with Claude
vision and transcribe it to plain text, which then feeds the normal parser.

Design: we *transcribe to text* rather than extract structured fields here, so
the existing `parser.parse_text` / funding-gate logic remains the single source
of truth for parsing. Images and scanned/image-only PDFs (which pypdf can't
read) are handled the same way.

Requires an ANTHROPIC_API_KEY with credits. Callers should catch
`VisionUnavailable` and degrade (e.g. fall back to pypdf text for PDFs).
"""
from __future__ import annotations

import base64
from pathlib import Path

from modules import config_loader

# Anthropic-supported image media types, keyed by file suffix.
IMAGE_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
PDF_MEDIA = "application/pdf"
SUPPORTED_SUFFIXES = tuple(IMAGE_MEDIA) + (".pdf",)

TRANSCRIBE_SYSTEM = (
    "You are an OCR/transcription engine for academic job and PhD opportunity "
    "postings. Transcribe ALL readable text from the image or document faithfully "
    "and completely. Preserve funding statements, the professor/PI name and email, "
    "the university/department/lab, deadlines, required documents, and any "
    "application links exactly as written. Do not summarise, interpret, translate, "
    "or add commentary — output only the transcribed text."
)
TRANSCRIBE_PROMPT = (
    "Transcribe the full text of this opportunity posting. Output plain text only."
)


class VisionUnavailable(RuntimeError):
    """Raised when vision transcription cannot run (no key, unsupported file, API error)."""


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_SUFFIXES


def _content_block(data: bytes, suffix: str) -> dict:
    b64 = base64.standard_b64encode(data).decode("ascii")
    if suffix in IMAGE_MEDIA:
        return {"type": "image",
                "source": {"type": "base64", "media_type": IMAGE_MEDIA[suffix], "data": b64}}
    if suffix == ".pdf":
        return {"type": "document",
                "source": {"type": "base64", "media_type": PDF_MEDIA, "data": b64}}
    raise VisionUnavailable(
        f"Unsupported file type {suffix!r}; use one of {', '.join(SUPPORTED_SUFFIXES)}."
    )


def transcribe_file(data: bytes, filename: str) -> str:
    """Transcribe an image/PDF (given as bytes) to text via Claude vision.

    Raises VisionUnavailable if there is no API key, the SDK is missing, the file
    type is unsupported, or the API call fails.
    """
    import os

    suffix = Path(filename).suffix.lower()
    block = _content_block(data, suffix)  # validates the type before any API setup

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise VisionUnavailable(
            "ANTHROPIC_API_KEY not set — image/scanned-PDF intake needs an LLM with credits."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise VisionUnavailable("anthropic SDK not installed.") from exc

    llm_cfg = config_loader.config().get("llm", {})
    model = llm_cfg.get("vision_model") or llm_cfg.get("model", "claude-opus-4-8")
    max_tokens = llm_cfg.get("vision_max_tokens", 4000)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=TRANSCRIBE_SYSTEM,
            messages=[{"role": "user",
                       "content": [block, {"type": "text", "text": TRANSCRIBE_PROMPT}]}],
        )
    except Exception as exc:
        raise VisionUnavailable(f"vision call failed: {exc}") from exc

    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise VisionUnavailable("vision returned no text.")
    return text
