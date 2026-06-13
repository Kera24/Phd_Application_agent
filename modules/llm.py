"""Thin Claude API wrapper used by parser/scoring/research/email modules.

Centralises model selection, JSON-mode prompting, and graceful degradation when
no API key is configured (so the dashboard and tests still run).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from modules import config_loader


class LLMUnavailable(RuntimeError):
    pass


def _chat(model: str, max_tokens: int, temperature: float):
    """Build a langchain-anthropic ChatAnthropic client (pinned LLM lib)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMUnavailable(
            "ANTHROPIC_API_KEY not set. Set it in the environment to enable LLM calls."
        )
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailable("langchain-anthropic not installed.") from exc
    return ChatAnthropic(model=model, max_tokens=max_tokens, temperature=temperature,
                         anthropic_api_key=api_key)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    """Return raw text completion via langchain-anthropic."""
    cfg = config_loader.config().get("llm", {})
    chat = _chat(
        model or cfg.get("model", "claude-opus-4-8"),
        max_tokens or cfg.get("max_tokens", 2000),
        cfg.get("temperature", 0.2) if temperature is None else temperature,
    )
    messages = []
    if system:
        messages.append(("system", system))
    else:
        messages.append(("system", "You are a precise research assistant. Never fabricate facts."))
    messages.append(("human", prompt))
    resp = chat.invoke(messages)
    return resp.content if isinstance(resp.content, str) else str(resp.content)


def complete_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Any:
    """Call the model and parse a JSON object/array from the reply.

    Robust to code fences and leading prose.
    """
    sys = (system or "") + (
        "\n\nRespond with ONLY valid JSON. No markdown, no commentary."
    )
    raw = complete(prompt, system=sys.strip(), model=model, max_tokens=max_tokens, temperature=0.0)
    return _extract_json(raw)


def _extract_json(raw: str) -> Any:
    raw = raw.strip()
    # Strip code fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Find the first balanced JSON object or array.
        start = min(
            [i for i in (raw.find("{"), raw.find("[")) if i != -1],
            default=-1,
        )
        if start == -1:
            raise
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] in "{[":
                depth += 1
            elif raw[i] in "}]":
                depth -= 1
                if depth == 0:
                    return json.loads(raw[start : i + 1])
        raise
