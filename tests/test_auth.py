from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.database import init_db
from app.main import app


def configure_auth(monkeypatch, enabled=True, password="secret", session_secret="test-secret"):
    init_db()
    monkeypatch.setattr(settings, "auth_enabled", enabled)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", password)
    monkeypatch.setattr(settings, "session_secret", session_secret)
    monkeypatch.setattr(settings, "session_cookie_name", "test_session")
    monkeypatch.setattr(settings, "session_max_age_seconds", 28800)


def test_dashboard_redirects_to_login_when_unauthenticated(monkeypatch):
    configure_auth(monkeypatch)
    client = TestClient(app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_succeeds_with_configured_credentials(monkeypatch):
    configure_auth(monkeypatch)
    client = TestClient(app)

    response = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "test_session" in response.cookies


def test_login_fails_with_wrong_password(monkeypatch):
    configure_auth(monkeypatch)
    client = TestClient(app)

    response = client.post("/login", data={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert "Sai thông tin" in response.text


def test_empty_admin_password_does_not_allow_login(monkeypatch):
    configure_auth(monkeypatch, password="")
    client = TestClient(app)

    response = client.post("/login", data={"username": "admin", "password": ""})

    assert response.status_code == 401


def test_authenticated_user_can_access_dashboard(monkeypatch):
    configure_auth(monkeypatch)
    client = TestClient(app)
    token = auth.create_session_token("admin")
    client.cookies.set("test_session", token)

    response = client.get("/")

    assert response.status_code == 200
    assert "TH Network Monitor" in response.text


def test_logout_clears_cookie(monkeypatch):
    configure_auth(monkeypatch)
    client = TestClient(app)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "test_session" in response.headers.get("set-cookie", "")


def test_admin_actions_blocked_when_unauthenticated(monkeypatch):
    configure_auth(monkeypatch)
    client = TestClient(app)

    monitor_response = client.post("/monitor/run-once", follow_redirects=False)
    telegram_response = client.post("/telegram/test", follow_redirects=False)
    store_create_response = client.post("/stores", follow_redirects=False)
    store_edit_response = client.post("/stores/1/edit", follow_redirects=False)

    assert monitor_response.status_code == 303
    assert monitor_response.headers["location"] == "/login"
    assert telegram_response.status_code == 303
    assert telegram_response.headers["location"] == "/login"
    assert store_create_response.status_code == 303
    assert store_create_response.headers["location"] == "/login"
    assert store_edit_response.status_code == 303
    assert store_edit_response.headers["location"] == "/login"


def test_auth_disabled_bypasses_login(monkeypatch):
    configure_auth(monkeypatch, enabled=False, password="", session_secret="change-me")
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
