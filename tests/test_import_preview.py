import re
from io import BytesIO
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Store


def configure_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "secret")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "session_cookie_name", "test_session")
    monkeypatch.setattr(settings, "session_max_age_seconds", 28800)


def make_db_override():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    store = Store(store_code="70000123", pc_name="OLD-PC")
    db.add(store)
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    return db


def clear_overrides():
    app.dependency_overrides.clear()


def authed_client():
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))
    return client


def excel_file(rows: list[dict]) -> BytesIO:
    output = BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False)
    output.seek(0)
    return output


def extract_token(html: str) -> str:
    match = re.search(r'name="token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_import_preview_redirects_to_login_when_unauthenticated(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    make_db_override()
    client = TestClient(app)

    response = client.post(
        "/import/preview",
        files={"file": ("stores.xlsx", excel_file([{"Mã CH": "70000127", "PC Name": "PC002"}]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        follow_redirects=False,
    )

    clear_overrides()
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_preview_does_not_commit_and_shows_counts(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    monkeypatch.setattr("app.main.PREVIEW_DIR", tmp_path)
    db = make_db_override()
    client = authed_client()

    response = client.post(
        "/import/preview",
        files={"file": ("stores.xlsx", excel_file([{"Mã CH": "70000123", "PC Name": "NEW-PC", "WAN DNS": "dns-001.example.com"}, {"Mã CH": "70000124", "PC Name": "PC002"}]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    store = db.query(Store).filter(Store.store_code == "70000123").first()
    clear_overrides()
    assert response.status_code == 200
    assert "Would create" in response.text
    assert "Would update" in response.text
    assert "70000124" in response.text
    assert store.pc_name == "OLD-PC"


def test_confirm_applies_import_and_deletes_pending_file(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    monkeypatch.setattr("app.main.PREVIEW_DIR", tmp_path)
    db = make_db_override()
    client = authed_client()
    preview_response = client.post(
        "/import/preview",
        files={"file": ("stores.xlsx", excel_file([{"Mã CH": "70000124", "PC Name": "PC002"}]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    token = extract_token(preview_response.text)
    pending_file = tmp_path / f"{token}.xlsx"

    response = client.post("/import/confirm", data={"token": token}, follow_redirects=False)
    created = db.query(Store).filter(Store.store_code == "70000124").first()

    clear_overrides()
    assert response.status_code == 303
    assert "created=1" in response.headers["location"]
    assert created is not None
    assert not pending_file.exists()


def test_cancel_deletes_pending_file_without_commit(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    monkeypatch.setattr("app.main.PREVIEW_DIR", tmp_path)
    db = make_db_override()
    client = authed_client()
    preview_response = client.post(
        "/import/preview",
        files={"file": ("stores.xlsx", excel_file([{"Mã CH": "70000125", "PC Name": "PC002"}]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    token = extract_token(preview_response.text)
    pending_file = tmp_path / f"{token}.xlsx"

    response = client.post("/import/cancel", data={"token": token}, follow_redirects=False)
    created = db.query(Store).filter(Store.store_code == "70000125").first()

    clear_overrides()
    assert response.status_code == 303
    assert response.headers["location"] == "/import?cancelled=1"
    assert created is None
    assert not pending_file.exists()


def test_invalid_preview_token_returns_404(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    monkeypatch.setattr("app.main.PREVIEW_DIR", tmp_path)
    make_db_override()
    client = authed_client()

    response = client.post("/import/confirm", data={"token": "../bad"})

    clear_overrides()
    assert response.status_code == 404


def test_post_import_alias_previews_without_commit(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    monkeypatch.setattr("app.main.PREVIEW_DIR", tmp_path)
    db = make_db_override()
    client = authed_client()

    response = client.post(
        "/import",
        files={"file": ("stores.xlsx", excel_file([{"Mã CH": "70000126", "PC Name": "PC002"}]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    created = db.query(Store).filter(Store.store_code == "70000126").first()

    clear_overrides()
    assert response.status_code == 200
    assert "Import Preview" in response.text
    assert created is None
