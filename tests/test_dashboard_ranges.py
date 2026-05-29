from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import settings
from app.database import Base, get_db
from app.main import app, dashboard_time_filter
from app.models import Incident, Store, StoreStatus


def configure_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "secret")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "session_cookie_name", "test_session")
    monkeypatch.setattr(settings, "session_max_age_seconds", 28800)
    monkeypatch.setattr(settings, "timezone", "UTC")


def make_db_override(now: datetime):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    stores = [
        Store(store_code="70000100", pc_name="DOWN-PC", wan_dns="down.example", ip_tunnel="10.0.0.1"),
        Store(store_code="70000101", pc_name="TUNNEL-PC", wan_dns="tunnel.example", ip_tunnel="10.0.0.2"),
        Store(store_code="70000102", pc_name="WAN-PC", wan_dns="wan.example", ip_tunnel="10.0.0.3"),
        Store(store_code="70000103", pc_name="NO-INCIDENT", wan_dns="ok.example", ip_tunnel="10.0.0.4"),
        Store(store_code="70000104", pc_name="OLD-PC", wan_dns="old.example", ip_tunnel="10.0.0.5"),
    ]
    db.add_all(stores)
    db.commit()
    for store in stores:
        db.refresh(store)
    db.add_all(
        [
            StoreStatus(store_id=stores[0].id, overall_status="DOWN", last_check_at=now),
            StoreStatus(store_id=stores[1].id, overall_status="TUNNEL_DOWN", last_check_at=now),
            StoreStatus(store_id=stores[2].id, overall_status="WAN_DOWN", last_check_at=now),
            StoreStatus(store_id=stores[3].id, overall_status="UP", last_check_at=now),
            StoreStatus(store_id=stores[4].id, overall_status="UP", last_check_at=now),
            Incident(store_id=stores[0].id, incident_type="DOWN", status="OPEN", started_at=now - timedelta(hours=2)),
            Incident(store_id=stores[1].id, incident_type="TUNNEL_DOWN", status="OPEN", started_at=now - timedelta(minutes=40)),
            Incident(store_id=stores[2].id, incident_type="WAN_DOWN", status="RESOLVED", started_at=now - timedelta(minutes=50), ended_at=now - timedelta(minutes=10)),
            Incident(store_id=stores[4].id, incident_type="DOWN", status="RESOLVED", started_at=now - timedelta(hours=4), ended_at=now - timedelta(hours=3)),
        ]
    )
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


def test_dashboard_time_filter_quick_range(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "UTC")
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)

    time_filter = dashboard_time_filter(quick_range="1h", now=now)

    assert time_filter["mode"] == "quick"
    assert time_filter["quick_range"] == "1h"
    assert time_filter["start_utc"] == datetime(2026, 5, 5, 11, 0, 0)
    assert time_filter["end_utc"] == datetime(2026, 5, 5, 12, 0, 0)


def test_dashboard_lists_incident_stores_by_range_and_type(monkeypatch):
    configure_auth(monkeypatch)
    now = datetime.now(UTC).replace(tzinfo=None)
    make_db_override(now)
    client = authed_client()

    response = client.get("/?quick_range=1h")

    clear_overrides()
    assert response.status_code == 200
    assert "100 cửa hàng đầu" not in response.text
    assert "Store DOWN" in response.text
    assert "Store TUNNEL_DOWN" in response.text
    assert "Store WAN_DOWN" in response.text
    assert "70000100" in response.text
    assert "70000101" in response.text
    assert "70000102" in response.text
    assert "70000103" not in response.text
    assert "70000104" not in response.text


def test_dashboard_absolute_range_uses_overlap(monkeypatch):
    configure_auth(monkeypatch)
    now = datetime(2026, 5, 5, 12, 0, 0)
    make_db_override(now)
    client = authed_client()

    response = client.get("/?time_mode=absolute&start=2026-05-05T11:30&end=2026-05-05T11:45")

    clear_overrides()
    assert response.status_code == 200
    assert "70000100" in response.text
    assert "70000101" in response.text
    assert "70000102" in response.text
    assert "70000104" not in response.text
