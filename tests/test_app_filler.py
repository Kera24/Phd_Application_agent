"""Phase 3 — assisted application filler (plan side). Hermetic, no browser/LLM."""
import pytest
from fastapi.testclient import TestClient

from db.models import Opportunity
from modules import app_filler, config_loader, http_cache

SAMPLE_HTML = """
<html><body>
  <form>
    <label for="fn">First name</label>
    <input type="text" id="fn" name="first_name" required>
    <label for="ln">Last name</label>
    <input type="text" id="ln" name="last_name">
    <label for="em">Email address</label>
    <input type="email" id="em" name="email">
    <label for="ph">Phone</label>
    <input type="tel" id="ph" name="phone">
    <label for="cntry">Country</label>
    <select id="cntry" name="country">
      <option value="">--</option>
      <option value="Australia">Australia</option>
      <option value="Germany">Germany</option>
    </select>
    <label for="cv">Upload CV</label>
    <input type="file" id="cv" name="cv_file">
    <label for="why">Why this lab?</label>
    <textarea id="why" name="motivation"></textarea>
    <input type="hidden" name="csrf" value="xyz">
    <input type="submit" value="Send">
  </form>
</body></html>
"""

PROFILE = {
    "name": "Raj Kumar Sah",
    "location": "Sydney, Australia",
    "contact": {"email": "raj@example.com", "phone": "+61400000000"},
}


def test_extract_form_fields():
    fields = app_filler.extract_form_fields(SAMPLE_HTML)
    by_name = {f["name"]: f for f in fields}
    # hidden + submit excluded
    assert "csrf" not in by_name
    assert set(by_name) == {"first_name", "last_name", "email", "phone",
                            "country", "cv_file", "motivation"}
    assert by_name["first_name"]["label"] == "First name"
    assert by_name["first_name"]["required"] is True
    assert by_name["country"]["type"] == "select"
    assert "Australia" in by_name["country"]["options"]
    assert by_name["cv_file"]["type"] == "file"


def test_heuristic_plan_maps_profile():
    fields = app_filler.extract_form_fields(SAMPLE_HTML)
    plan = app_filler.build_fill_plan(None, PROFILE, fields)  # keyless -> heuristic
    by_name = {p["name"]: p for p in plan}
    assert by_name["first_name"]["value"] == "Raj"
    assert by_name["last_name"]["value"] == "Sah"
    assert by_name["email"]["value"] == "raj@example.com"
    assert by_name["phone"]["value"] == "+61400000000"
    assert by_name["country"]["value"] == "Australia"
    # file field -> upload, essay -> needs human
    assert by_name["cv_file"]["document"] == "cv"
    assert by_name["motivation"]["needs_human"] is True


# --- endpoint ---------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config_loader, "profile", lambda: PROFILE)
    monkeypatch.setattr(http_cache, "get", lambda url, **kw: SAMPLE_HTML)
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_fill_plan_endpoint(client):
    # seed an opportunity with an application link
    from api.main import dbsession
    with dbsession.session_scope() as s:
        opp = Opportunity(source_type="web", opportunity_type="advertised",
                          application_link="https://apply.example.edu/form")
        s.add(opp); s.flush()
        oid = opp.id
    r = client.post(f"/opportunities/{oid}/fill-plan", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["field_count"] == 7
    plan = {p["name"]: p for p in body["plan"]}
    assert plan["email"]["value"] == "raj@example.com"


def test_fill_plan_requires_link(client):
    from api.main import dbsession
    with dbsession.session_scope() as s:
        opp = Opportunity(source_type="web", opportunity_type="advertised")
        s.add(opp); s.flush()
        oid = opp.id
    r = client.post(f"/opportunities/{oid}/fill-plan", json={})
    assert r.status_code == 400


def test_fill_plan_unknown_opportunity(client):
    r = client.post("/opportunities/99999/fill-plan", json={})
    assert r.status_code == 404
