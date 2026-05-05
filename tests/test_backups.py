from pathlib import Path

from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app


def configure_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "secret")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "session_cookie_name", "test_session")
    monkeypatch.setattr(settings, "session_max_age_seconds", 28800)


def configure_paths(monkeypatch, tmp_path):
    db_path = tmp_path / "network_monitor.db"
    backup_path = tmp_path / "backups"
    db_path.write_bytes(b"active-db")
    backup_path.mkdir()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr("app.backups.backup_dir", lambda: backup_path)
    monkeypatch.setattr("app.main.list_backups", __import__("app.backups", fromlist=["list_backups"]).list_backups)
    monkeypatch.setattr("app.main.resolve_backup", __import__("app.backups", fromlist=["resolve_backup"]).resolve_backup)
    monkeypatch.setattr("app.main.create_sqlite_backup", __import__("app.backups", fromlist=["create_sqlite_backup"]).create_sqlite_backup)
    monkeypatch.setattr("app.main.restore_sqlite_backup", __import__("app.backups", fromlist=["restore_sqlite_backup"]).restore_sqlite_backup)
    monkeypatch.setattr("app.main.sqlite_db_path", __import__("app.backups", fromlist=["sqlite_db_path"]).sqlite_db_path)
    return db_path, backup_path


def authed_client():
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))
    return client


def test_backups_redirects_to_login_when_unauthenticated(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    configure_paths(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/backups", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_backups_page_renders_backup_table(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    _db_path, backup_dir = configure_paths(monkeypatch, tmp_path)
    (backup_dir / "network_monitor_manual_20260101_000000.db").write_bytes(b"backup")
    client = authed_client()

    response = client.get("/backups")

    assert response.status_code == 200
    assert "SQLite Backups" in response.text
    assert "network_monitor_manual_20260101_000000.db" in response.text


def test_manual_backup_creates_file(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    _db_path, backup_dir = configure_paths(monkeypatch, tmp_path)
    client = authed_client()

    response = client.post("/backups/create", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/backups?created=1"
    assert list(backup_dir.glob("network_monitor_manual_*.db"))


def test_download_backup_returns_attachment(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    _db_path, backup_dir = configure_paths(monkeypatch, tmp_path)
    backup = backup_dir / "network_monitor_manual_20260101_000000.db"
    backup.write_bytes(b"backup-content")
    client = authed_client()

    response = client.get(f"/backups/download/{backup.name}")

    assert response.status_code == 200
    assert response.content == b"backup-content"
    assert "attachment" in response.headers["content-disposition"]


def test_delete_backup_removes_file(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    _db_path, backup_dir = configure_paths(monkeypatch, tmp_path)
    backup = backup_dir / "network_monitor_manual_20260101_000000.db"
    backup.write_bytes(b"backup-content")
    client = authed_client()

    response = client.post("/backups/delete", data={"name": backup.name}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/backups?deleted=1"
    assert not backup.exists()


def test_restore_backup_replaces_db_and_creates_pre_restore(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    db_path, backup_dir = configure_paths(monkeypatch, tmp_path)
    backup = backup_dir / "network_monitor_manual_20260101_000000.db"
    backup.write_bytes(b"restored-db")
    client = authed_client()

    response = client.post("/backups/restore", data={"name": backup.name}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/backups?restored=1&pre_restore=")
    assert db_path.read_bytes() == b"restored-db"
    assert list(backup_dir.glob("network_monitor_pre_restore_*.db"))


def test_backup_path_traversal_returns_404(monkeypatch, tmp_path):
    configure_auth(monkeypatch)
    configure_paths(monkeypatch, tmp_path)
    client = authed_client()

    response = client.get("/backups/download/../evil.db")

    assert response.status_code == 404


def test_non_sqlite_provider_disables_actions(monkeypatch):
    configure_auth(monkeypatch)
    monkeypatch.setattr(settings, "database_url", "postgresql://example/db")
    client = authed_client()

    page = client.get("/backups")
    create = client.post("/backups/create", follow_redirects=False)

    assert page.status_code == 200
    assert "supports SQLite only" in page.text
    assert create.status_code == 303
    assert create.headers["location"] == "/backups?unsupported=1"
