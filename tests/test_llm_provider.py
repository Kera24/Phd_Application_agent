"""Provider-agnostic LLM selection (OpenAI vs Anthropic). No network."""
from modules import llm


def test_no_keys_unavailable(monkeypatch):
    assert llm.provider() is None
    assert llm.available() is False


def test_openai_selected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert llm.provider() == "openai"
    assert llm.available() is True


def test_anthropic_selected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.provider() == "anthropic"


def test_openai_preferred_when_both(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert llm.provider() == "openai"


def test_provider_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm.provider() == "anthropic"


def test_resolve_model(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    # OpenAI ignores a Claude id and uses an OpenAI default
    assert not llm._resolve_model("openai", "claude-opus-4-8").startswith("claude")
    assert llm._resolve_model("openai", "gpt-4.1") == "gpt-4.1"
    assert llm._resolve_model("openai", None) == "gpt-4o"
    # Anthropic passes a Claude id through
    assert llm._resolve_model("anthropic", "claude-sonnet-4-6") == "claude-sonnet-4-6"
