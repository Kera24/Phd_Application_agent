"""Applicant profile: DB-over-YAML merge, endpoints, and new field mappings."""
import pytest
from fastapi.testclient import TestClient

from modules import app_filler, config_loader


def test_deep_merge_nested_and_scalars():
    base = {"name": "A", "contact": {"email": "", "phone": "p"}, "keep": 1}
    over = {"name": "B", "contact": {"email": "b@x.com"}}
    merged = config_loader._deep_merge(base, over)
    assert merged["name"] == "B"
    assert merged["contact"]["email"] == "b@x.com"
    assert merged["contact"]["phone"] == "p"   # untouched
    assert merged["keep"] == 1                  # YAML-only key preserved


def test_profile_merges_db_over_yaml(db):
    assert config_loader.applicant_overrides() == {}
    config_loader.save_applicant({"name": "Test Student",
                                  "contact": {"email": "t@x.com"},
                                  "test_scores": {"ielts": "8.0"}})
    merged = config_loader.profile()
    assert merged["name"] == "Test Student"
    assert merged["contact"]["email"] == "t@x.com"
    assert "research_projects" in merged   # YAML base still present


def test_heuristic_maps_new_fields():
    html = """<form>
      <label for="dob">Date of birth</label><input id="dob" name="dob">
      <label for="nat">Nationality</label><input id="nat" name="nat">
      <label for="ie">IELTS score</label><input id="ie" name="ie">
      <label for="rn">Referee 1 name</label><input id="rn" name="rn">
      <label for="re">Referee 1 email</label><input id="re" name="re">
      <label for="gpa">CGPA</label><input id="gpa" name="gpa">
      <label for="orc">ORCID</label><input id="orc" name="orc">
    </form>"""
    profile = {
        "date_of_birth": "1999-01-01", "nationality": "Nepal", "gpa": "3.9/4.0",
        "contact": {"orcid": "0000-0001"},
        "test_scores": {"ielts": "8.0"},
        "referees": [{"name": "Dr. A", "email": "a@uni.edu", "institution": "Uni"}],
    }
    fields = app_filler.extract_form_fields(html)
    plan = {p["name"]: p for p in app_filler.build_fill_plan(None, profile, fields)}
    assert plan["dob"]["value"] == "1999-01-01"
    assert plan["nat"]["value"] == "Nepal"
    assert plan["ie"]["value"] == "8.0"
    assert plan["rn"]["value"] == "Dr. A"
    assert plan["re"]["value"] == "a@uni.edu"
    assert plan["gpa"]["value"] == "3.9/4.0"
    assert plan["orc"]["value"] == "0000-0001"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_profile_endpoints_roundtrip(client):
    r = client.put("/profile", json={"name": "Stu", "contact": {"email": "s@x.com"}})
    assert r.status_code == 200, r.text
    g = client.get("/profile").json()
    assert g["overrides"]["name"] == "Stu"
    assert g["profile"]["contact"]["email"] == "s@x.com"


def test_new_document_kind_accepted(client):
    import io
    files = {"file": ("ref.pdf", io.BytesIO(b"%PDF-1.4 recommendation"), "application/pdf")}
    r = client.post("/assets", data={"kind": "recommendation"}, files=files)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "recommendation"
