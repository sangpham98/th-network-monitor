from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.config import settings
from app.database import Base, get_db
from app.main import app, display_overall_status, display_tunnel_status, display_wan_status
from app.models import Incident, Store, StoreStatus


def utc_now() -> datetime:
    return datetime(2026, 5, 5, 8, 30, 0, tzinfo=UTC).replace(tzinfo=None)


def configure_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "secret")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "session_cookie_name", "test_session")
    monkeypatch.setattr(settings, "session_max_age_seconds", 28800)


def make_db_override():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    store = Store(
        store_code="CH001",
        pc_name="PC001",
        wan_dns="wan.example",
        ip_tunnel="10.0.0.1",
        ip_local="192.168.1.10",
        region="HCM",
        area="Area A",
        address="123 Test Street",
    )
    db.add(store)
    db.commit()
    db.refresh(store)
    db.add(
        StoreStatus(
            store_id=store.id,
            wan_status="UP",
            tunnel_status="DOWN",
            overall_status="TUNNEL_DOWN",
            wan_success_count=2,
            tunnel_fail_count=4,
            tunnel_down_window="1111",
            last_check_at=utc_now(),
        )
    )
    db.add(Incident(store_id=store.id, incident_type="TUNNEL_DOWN", status="OPEN", started_at=utc_now(), alert_sent=True))
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    return store


def clear_overrides():
    app.dependency_overrides.clear()


def test_store_detail_redirects_to_login_when_unauthenticated(monkeypatch):
    configure_auth(monkeypatch)
    store = make_db_override()
    client = TestClient(app)

    response = client.get(f"/stores/{store.id}", follow_redirects=False)

    clear_overrides()
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_user_can_open_store_detail(monkeypatch):
    configure_auth(monkeypatch)
    store = make_db_override()
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))

    response = client.get(f"/stores/{store.id}")

    clear_overrides()
    assert response.status_code == 200
    assert "Store CH001" in response.text
    assert "TUNNEL_DOWN" in response.text
    assert "wan.example" in response.text
    assert "2026-05-05 15:30:00" in response.text


def test_gui_display_uses_current_db_status_directly():
    store = Store(store_code="CH001", wan_dns="wan.example", ip_tunnel="10.0.0.1")
    store.status = StoreStatus(wan_status="DOWN", tunnel_status="UP", overall_status="WAN_DOWN")

    assert display_wan_status(store) == "DOWN"
    assert display_tunnel_status(store) == "UP"
    assert display_overall_status(store) == "WAN_DOWN"


def test_gui_display_unknown_for_missing_status_or_target():
    store = Store(store_code="CH001", wan_dns="wan.example", ip_tunnel=None)

    assert display_wan_status(store) == "UNKNOWN"
    assert display_tunnel_status(store) == "UNKNOWN"
    assert display_overall_status(store) == "UNKNOWN"

    store.status = StoreStatus(wan_status="UP", tunnel_status="DOWN", overall_status="UP")
    assert display_wan_status(store) == "UP"
    assert display_tunnel_status(store) == "UNKNOWN"
    assert display_overall_status(store) == "UP"


def test_store_detail_missing_store_returns_404(monkeypatch):
    configure_auth(monkeypatch)
    make_db_override()
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))

    response = client.get("/stores/999")

    clear_overrides()
    assert response.status_code == 404


def test_store_table_links_to_detail_page(monkeypatch):
    configure_auth(monkeypatch)
    store = make_db_override()
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))

    response = client.get("/stores")

    clear_overrides()
    assert response.status_code == 200
    assert f'href="/stores/{store.id}"' in response.text
    assert f'action="/stores/{store.id}/delete"' in response.text


def test_dashboard_counts_and_store_filter_use_display_status(monkeypatch):
    configure_auth(monkeypatch)
    make_db_override()
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))

    dashboard_response = client.get("/")
    tunnel_response = client.get("/stores?status=TUNNEL_DOWN")
    up_response = client.get("/stores?status=UP")

    clear_overrides()
    assert dashboard_response.status_code == 200
    assert '<div class="num status-TUNNEL_DOWN">1</div>' in dashboard_response.text
    assert tunnel_response.status_code == 200
    assert "CH001" in tunnel_response.text
    assert up_response.status_code == 200
    assert "CH001" not in up_response.text


def test_store_delete_redirects_to_login_when_unauthenticated(monkeypatch):
    configure_auth(monkeypatch)
    store = make_db_override()
    client = TestClient(app)

    response = client.post(f"/stores/{store.id}/delete", follow_redirects=False)

    clear_overrides()
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_user_can_delete_store(monkeypatch):
    configure_auth(monkeypatch)
    store = make_db_override()
    db = next(app.dependency_overrides[get_db]())
    client = TestClient(app)
    client.cookies.set("test_session", auth.create_session_token("admin"))

    response = client.post(f"/stores/{store.id}/delete", follow_redirects=False)

    remaining_store = db.query(Store).filter(Store.id == store.id).first()
    remaining_status = db.query(StoreStatus).filter(StoreStatus.store_id == store.id).first()
    remaining_incidents = db.query(Incident).filter(Incident.store_id == store.id).count()
    clear_overrides()
    assert response.status_code == 303
    assert response.headers["location"] == "/stores?deleted=1"
    assert remaining_store is None
    assert remaining_status is None
    assert remaining_incidents == 0
