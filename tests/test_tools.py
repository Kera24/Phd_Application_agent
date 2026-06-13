"""LangChain tool unit tests: typed args, explicit ok/error returns, send gate."""
from db.models import Professor
from modules import config_loader
from agent import tools


def _invoke(tool, **kwargs):
    return tool.invoke(kwargs)


def test_timezone_resolver_tool():
    out = _invoke(tools.timezone_resolver_tool, university="ETH Zurich")
    assert out["ok"] and out["zone"] == "Europe/Zurich" and out["flagged"] is False


def test_funding_evidence_tool_detects_signals():
    out = _invoke(tools.funding_evidence_tool,
                  text="This position is funded by an ERC Starting Grant and DFG.")
    assert out["ok"] and out["likely_funded"]
    assert "ERC" in out["signals"] and "DFG" in out["signals"]


def test_funding_evidence_tool_no_signals():
    out = _invoke(tools.funding_evidence_tool, text="A nice lab with good coffee.")
    assert out["ok"] and not out["likely_funded"] and out["signals"] == []


def test_gmail_send_tool_hard_gated(monkeypatch):
    base = config_loader.config()
    base["approved_send_mode"] = False
    monkeypatch.setattr(config_loader, "config", lambda: base)
    out = _invoke(tools.gmail_send_tool, to="x@uni.edu", subject="s", body="b")
    assert out["ok"] is False
    assert "approved_send_mode" in out["error"]


def test_duplicate_check_tool(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane Doe", email="jane@uni.edu", university="Uni X"))
    out = _invoke(tools.duplicate_check_tool, professor_email="jane@uni.edu",
                  professor_name="Jane Doe", university="Uni X")
    assert out["ok"] and out["professor_exists"] and out["professor_id"]


def test_document_reader_tool_missing_file():
    out = _invoke(tools.document_reader_tool, file_path="does_not_exist.pdf")
    # Reads gracefully: empty text + scanned-warning, never raises.
    assert out["ok"] and out["char_count"] == 0
