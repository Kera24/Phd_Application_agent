"""Hosted Gmail web OAuth: token storage + auth-URL building (offline)."""
import pytest
from fastapi.testclient import TestClient

from modules import gmail_client


def test_token_storage_roundtrip(db):
    gmail_client._save_token_dict({"token": "abc", "refresh_token": "r"})
    assert gmail_client.is_authorised() is True
    assert gmail_client._load_token_dict()["token"] == "abc"


def test_build_auth_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    url, state = gmail_client.build_auth_url("https://x.test/gmail/callback")
    assert "accounts.google.com" in url
    assert "cid.apps.googleusercontent.com" in url
    assert state


def test_build_auth_url_requires_client(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    # also no web secrets file in the test env
    with pytest.raises(gmail_client.GmailNotAuthorised):
        gmail_client.build_auth_url("https://x.test/gmail/callback")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_gmail_status(client):
    r = client.get("/gmail/status")
    assert r.status_code == 200
    assert isinstance(r.json()["authorised"], bool)


def test_gmail_authorize_returns_url(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x.test")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    r = client.get("/gmail/authorize")
    assert r.status_code == 200, r.text
    assert "accounts.google.com" in r.json()["auth_url"]


def test_gmail_authorize_needs_base_url(client, monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    r = client.get("/gmail/authorize")
    assert r.status_code == 400
