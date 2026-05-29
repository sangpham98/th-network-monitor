from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Incident, Store
from monitor import worker
from monitor.status_engine import format_current_incidents_summary, update_status_and_incident


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return session


def make_store(db, store_code="CH001", enabled=True):
    store = Store(
        store_code=store_code,
        pc_name=f"PC{store_code[-3:]}",
        wan_dns="wan.example",
        ip_tunnel="10.0.0.1",
        area="Area 1",
        region="North",
        enabled=enabled,
    )
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def test_status_updates_do_not_depend_on_alert_sent_flags():
    db = make_db()
    store = make_store(db)

    update_status_and_incident(db, store, False, False)
    db.commit()
    changed, _status, _old, _recovered, incident_ids = update_status_and_incident(db, store, False, False)
    db.commit()

    assert changed is True
    assert incident_ids
    incident = db.query(Incident).one()
    incident.alert_sent = True
    db.commit()

    changed, _status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, True)
    db.commit()

    assert changed is True
    assert recovered is True
    assert incident_ids == [incident.id]
    assert incident.status == "RESOLVED"


def test_current_open_incident_events_include_enabled_open_incidents():
    db = make_db()
    store = make_store(db)
    disabled_store = make_store(db, "CH002", enabled=False)
    open_incident = Incident(store_id=store.id, incident_type="TUNNEL_DOWN", status="OPEN", started_at=utc_now())
    db.add_all(
        [
            open_incident,
            Incident(store_id=store.id, incident_type="DOWN", status="RESOLVED", started_at=utc_now()),
            Incident(store_id=disabled_store.id, incident_type="DOWN", status="OPEN", started_at=utc_now()),
        ]
    )
    db.commit()

    events = worker._current_open_incident_events(db)

    assert len(events) == 1
    assert events[0]["store_code"] == "CH001"
    assert events[0]["status"] == "TUNNEL_DOWN"
    assert events[0]["incident_ids"] == [open_incident.id]
    assert events[0]["kind"] == "summary"


def test_due_telegram_summary_slot_tracks_each_slot_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "STATUS_PATH", tmp_path / "monitor_status.json")
    now = datetime(2026, 5, 28, 9, 1, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

    assert worker._due_telegram_summary_slot(now) == "09:00"
    worker._mark_telegram_summary_slot_sent("09:00", now)
    assert worker._due_telegram_summary_slot(now) is None

    afternoon = datetime(2026, 5, 28, 14, 1, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert worker._due_telegram_summary_slot(afternoon) == "14:00"
    worker._mark_telegram_summary_slot_sent("14:00", afternoon)
    assert worker._due_telegram_summary_slot(afternoon) is None

    next_day = datetime(2026, 5, 29, 9, 1, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert worker._due_telegram_summary_slot(next_day) == "09:00"


def test_current_incidents_summary_sends_ok_for_empty_slot():
    message = format_current_incidents_summary([], "09:00")

    assert "TH NETWORK OK" in message
    assert "09:00" in message
    assert "Không có store đang incident" in message


def test_current_incidents_summary_lists_open_stores():
    events = [
        {"store_code": "CH001", "status": "DOWN", "region": "North", "area": "Area 1"},
        {"store_code": "CH002", "status": "WAN_DOWN", "region": "North", "area": "Area 2"},
    ]

    message = format_current_incidents_summary(events, "14:00")

    assert "TH NETWORK INCIDENT SUMMARY" in message
    assert "Tổng affected: <b>2</b>" in message
    assert "CH001" in message
    assert "CH002" in message
