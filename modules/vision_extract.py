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

from modules import config_loader, llm

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
    """Transcribe an image/PDF (given as bytes) to text via the active provider.

    Uses OpenAI when OPENAI_API_KEY is set, else Anthropic. Raises VisionUnavailable
    if there is no key, the SDK is missing, the file type isn't supported by the
    provider, or the API call fails.
    """
    suffix = Path(filename).suffix.lower()
    if not is_supported(filename):
        raise VisionUnavailable(
            f"Unsupported file type {suffix!r}; use {', '.join(SUPPORTED_SUFFIXES)}.")
    prov = llm.provider()
    if prov is None:
        raise VisionUnavailable(
            "No LLM API key set — image/scanned-PDF intake needs OpenAI or Anthropic.")
    if prov == "openai":
        return _transcribe_openai(data, suffix)
    return _transcribe_anthropic(data, suffix)


def _transcribe_anthropic(data: bytes, suffix: str) -> str:
    import os
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise VisionUnavailable("anthropic SDK not installed.") from exc
    llm_cfg = config_loader.config().get("llm", {})
    model = llm_cfg.get("vision_model") or llm_cfg.get("model", "claude-opus-4-8")
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=model, max_tokens=llm_cfg.get("vision_max_tokens", 4000),
            system=TRANSCRIBE_SYSTEM,
            messages=[{"role": "user",
                       "content": [_content_block(data, suffix),
                                   {"type": "text", "text": TRANSCRIBE_PROMPT}]}])
    except Exception as exc:
        raise VisionUnavailable(f"vision call failed: {exc}") from exc
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise VisionUnavailable("vision returned no text.")
    return text


def _transcribe_openai(data: bytes, suffix: str) -> str:
    import os
    if suffix not in IMAGE_MEDIA:
        raise VisionUnavailable(
            "PDF vision via OpenAI isn't supported here — upload an image, or a "
            "text-based PDF (which is read directly without vision).")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise VisionUnavailable("openai SDK not installed.") from exc
    cfg = config_loader.config().get("llm", {})
    model = (os.environ.get("OPENAI_VISION_MODEL") or cfg.get("openai_vision_model")
             or os.environ.get("OPENAI_MODEL") or cfg.get("openai_model") or "gpt-4o")
    b64 = base64.standard_b64encode(data).decode("ascii")
    data_url = f"data:{IMAGE_MEDIA[suffix]};base64,{b64}"
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model=model, max_tokens=cfg.get("vision_max_tokens", 4000),
            messages=[{"role": "system", "content": TRANSCRIBE_SYSTEM},
                      {"role": "user", "content": [
                          {"type": "text", "text": TRANSCRIBE_PROMPT},
                          {"type": "image_url", "image_url": {"url": data_url}}]}])
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        raise VisionUnavailable(f"vision call failed: {exc}") from exc
    if not text:
        raise VisionUnavailable("vision returned no text.")
    return text
