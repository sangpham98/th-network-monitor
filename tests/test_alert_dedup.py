from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Incident, Store, StoreStatus
from monitor import worker
from monitor.status_engine import update_status_and_incident


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return session


def make_store(db):
    store = Store(store_code="CH001", pc_name="PC001", wan_dns="wan.example", ip_tunnel="10.0.0.1")
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def test_alert_sent_incident_does_not_generate_duplicate_event():
    db = make_db()
    store = make_store(db)

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, False, 1, 2)
    db.commit()
    assert changed is True
    assert incident_ids

    incident = db.query(Incident).one()
    incident.alert_sent = True
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, False, 1, 2)
    db.commit()

    assert incident_ids == []
    assert db.query(Incident).filter(Incident.status == "OPEN").count() == 1


def test_mark_alert_sent_sets_alert_flag_and_last_alert_at(monkeypatch):
    db = make_db()
    store = make_store(db)
    status = StoreStatus(store_id=store.id)
    incident = Incident(store_id=store.id, incident_type="DOWN", status="OPEN", started_at=utc_now())
    db.add_all([status, incident])
    db.commit()

    monkeypatch.setattr(worker, "SessionLocal", lambda: db)

    worker._mark_alert_sent([incident.id], recovered=False)

    assert incident.alert_sent is True
    assert incident.recovery_sent is False
    assert status.last_alert_at == incident.started_at


def test_mark_recovery_sent_sets_recovery_flag_and_last_alert_at(monkeypatch):
    db = make_db()
    store = make_store(db)
    ended_at = utc_now()
    status = StoreStatus(store_id=store.id)
    incident = Incident(
        store_id=store.id,
        incident_type="DOWN",
        status="RESOLVED",
        started_at=utc_now(),
        ended_at=ended_at,
    )
    db.add_all([status, incident])
    db.commit()

    monkeypatch.setattr(worker, "SessionLocal", lambda: db)

    worker._mark_alert_sent([incident.id], recovered=True)

    assert incident.alert_sent is False
    assert incident.recovery_sent is True
    assert status.last_alert_at == ended_at


def test_pending_open_alert_events_include_old_unsent_incidents():
    db = make_db()
    store = make_store(db)
    incident = Incident(store_id=store.id, incident_type="TUNNEL_DOWN", status="OPEN", started_at=utc_now())
    db.add(incident)
    db.commit()

    events = worker._pending_open_alert_events(db, set())

    assert len(events) == 1
    assert events[0]["store_code"] == "CH001"
    assert events[0]["status"] == "TUNNEL_DOWN"
    assert events[0]["incident_ids"] == [incident.id]
    assert events[0]["recovered"] is False


def test_pending_open_alert_events_include_null_alert_sent_incidents():
    db = make_db()
    store = make_store(db)
    incident = Incident(store_id=store.id, incident_type="DOWN", status="OPEN", started_at=utc_now(), alert_sent=None)
    db.add(incident)
    db.commit()

    events = worker._pending_open_alert_events(db, set())

    assert len(events) == 1
    assert events[0]["incident_ids"] == [incident.id]


def test_pending_open_alert_events_exclude_current_cycle_incidents():
    db = make_db()
    store = make_store(db)
    incident = Incident(store_id=store.id, incident_type="DOWN", status="OPEN", started_at=utc_now())
    db.add(incident)
    db.commit()

    events = worker._pending_open_alert_events(db, {incident.id})

    assert events == []
