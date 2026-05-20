from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Incident, Store, StoreStatus
from monitor.status_engine import format_alert_event, format_alert_summary, format_major_incident, update_status_and_incident


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return session


def make_store(db, *, wan_dns="wan.example", ip_tunnel="10.0.0.1"):
    store = Store(store_code="70000123", pc_name="PC001", wan_dns=wan_dns, ip_tunnel=ip_tunnel)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def test_alert_formatters_accept_worker_events():
    event = {
        "store_code": "70000123",
        "pc_name": "PC001",
        "region": "North",
        "area": "Area 1",
        "address": "123 Main",
        "status": "WAN_DOWN",
        "wan_dns": "wan.example",
        "ip_tunnel": "10.0.0.1",
        "incident_ids": [42],
        "recovered": False,
    }

    alert = format_alert_event(event)
    recovery = format_alert_event(event, recovered=True)
    summary = format_alert_summary([event])
    major = format_major_incident([event])

    assert "🚨 TH TRUEMART ALERT" in alert
    assert "🏪 Store: <b>70000123</b>" in alert
    assert "🆔 Incident: 42" in alert
    assert "✅ TH TRUEMART RECOVERY" in recovery
    assert "🚨 TH NETWORK ALERT SUMMARY" in summary
    assert "🔥 TH NETWORK MAJOR INCIDENT" in major


def test_wan_failure_opens_incident_immediately():
    db = make_db()
    store = make_store(db)

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, True)
    db.commit()

    assert changed is True
    assert recovered is False
    assert status == "WAN_DOWN"
    assert store.status.wan_status == "DOWN"
    assert store.status.tunnel_status == "UP"
    assert len(incident_ids) == 1
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 1


def test_both_targets_failed_sets_down_immediately():
    db = make_db()
    store = make_store(db)

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, False)
    db.commit()

    assert changed is True
    assert recovered is False
    assert status == "DOWN"
    assert incident_ids
    assert store.status.overall_status == "DOWN"


def test_open_incident_type_updates_immediately():
    db = make_db()
    store = make_store(db)

    update_status_and_incident(db, store, False, True)
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, False)
    db.commit()

    assert changed is True
    assert recovered is False
    assert status == "DOWN"
    assert incident_ids
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    assert incident.incident_type == "DOWN"


def test_recovery_resolves_immediately_after_successful_round():
    db = make_db()
    store = make_store(db)

    update_status_and_incident(db, store, False, False)
    db.commit()
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    incident.alert_sent = True
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, True)
    db.commit()

    assert changed is True
    assert status == "UP"
    assert recovered is True
    assert incident_ids == [incident.id]
    assert db.query(Incident).filter(Incident.status == "OPEN").count() == 0
    resolved = db.query(Incident).filter(Incident.status == "RESOLVED").one()
    assert resolved.duration_seconds is not None


def test_recovery_does_not_notify_if_down_alert_was_never_sent():
    db = make_db()
    store = make_store(db)

    update_status_and_incident(db, store, False, False)
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, True)
    db.commit()

    assert changed is True
    assert status == "UP"
    assert recovered is True
    assert incident_ids == []
    assert db.query(Incident).filter(Incident.status == "RESOLVED").count() == 1


def test_wan_only_store_recovers_immediately():
    db = make_db()
    store = make_store(db, ip_tunnel=None)

    update_status_and_incident(db, store, False, None)
    db.commit()
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    incident.alert_sent = True
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, None)
    db.commit()

    assert changed is True
    assert status == "UP"
    assert recovered is True
    assert incident_ids == [incident.id]


def test_tunnel_only_store_recovers_immediately():
    db = make_db()
    store = make_store(db, wan_dns=None)

    update_status_and_incident(db, store, None, False)
    db.commit()
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    incident.alert_sent = True
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, None, True)
    db.commit()

    assert changed is True
    assert status == "UP"
    assert recovered is True
    assert incident_ids == [incident.id]


def test_no_recovery_when_no_targets_are_configured():
    db = make_db()
    store = make_store(db, wan_dns=None, ip_tunnel=None)

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, None, None)
    db.commit()

    assert changed is False
    assert status == "UNKNOWN"
    assert recovered is False
    assert incident_ids == []


def test_counters_still_track_current_rounds():
    db = make_db()
    store = make_store(db)

    update_status_and_incident(db, store, False, True)
    update_status_and_incident(db, store, True, False)
    db.commit()

    assert store.status.wan_success_count == 1
    assert store.status.wan_fail_count == 0
    assert store.status.tunnel_success_count == 0
    assert store.status.tunnel_fail_count == 1
    assert store.status.wan_down_window == "10"
    assert store.status.tunnel_down_window == "01"
