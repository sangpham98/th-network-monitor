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


def make_store(db):
    store = Store(store_code="70000123", pc_name="PC001", wan_dns="wan.example", ip_tunnel="10.0.0.1")
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

    assert "TH TRUEMART ALERT" in alert
    assert "70000123" in alert
    assert "42" in alert
    assert "RECOVERY" in recovery
    assert "TH NETWORK ALERT SUMMARY" in summary
    assert "TH NETWORK MAJOR INCIDENT" in major



def test_down_threshold_and_recovery():
    db = make_db()
    store = make_store(db)

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, True, 2, 2)
    db.commit()
    assert status == "UNKNOWN"
    assert changed is False
    assert store.status.wan_status == "DOWN"
    assert store.status.tunnel_status == "UP"
    assert incident_ids == []

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, True, 2, 2)
    db.commit()
    assert status == "WAN_DOWN"
    assert changed is True
    assert len(incident_ids) == 1
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 1

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, True, 2, 2)
    db.commit()
    assert status == "WAN_DOWN"
    assert recovered is False
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 1


def test_down_threshold_uses_four_of_five_window():
    db = make_db()
    store = make_store(db)

    for wan_ok in [False, False, True, False]:
        changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, wan_ok, True, 4, 2)
        db.commit()
        assert incident_ids == []

    assert store.status.wan_down_window == "1101"
    assert store.status.overall_status == "UNKNOWN"
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 0

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, True, 4, 2)
    db.commit()

    assert store.status.wan_down_window == "11011"
    assert status == "WAN_DOWN"
    assert len(incident_ids) == 1
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 1


def test_stale_fail_window_does_not_alert_when_current_check_succeeds():
    db = make_db()
    store = make_store(db)
    store.status = StoreStatus(store_id=store.id, wan_down_window="1111")
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, False, 4, 2)
    db.commit()

    assert store.status.wan_down_window == "11110"
    assert status == "UNKNOWN"
    assert store.status.wan_status == "UP"
    assert store.status.tunnel_status == "DOWN"
    assert incident_ids == []
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 0


def test_confirmed_up_store_stays_up_during_pending_raw_failure():
    db = make_db()
    store = make_store(db)
    store.status = StoreStatus(store_id=store.id, overall_status="UP")
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, True, 4, 2)
    db.commit()

    assert status == "UP"
    assert changed is False
    assert store.status.wan_status == "DOWN"
    assert store.status.tunnel_status == "UP"
    assert incident_ids == []
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 0


def test_partial_confirmed_down_does_not_expand_until_other_target_confirms():
    db = make_db()
    store = make_store(db)
    store.status = StoreStatus(store_id=store.id, overall_status="WAN_DOWN", wan_down_window="1111")
    db.add(Incident(store_id=store.id, incident_type="WAN_DOWN", status="OPEN"))
    db.commit()

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, False, False, 4, 2)
    db.commit()

    assert status == "WAN_DOWN"
    assert changed is False
    assert store.status.tunnel_status == "DOWN"
    assert store.status.tunnel_down_window == "1"
    assert incident_ids == []
    assert db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").count() == 1


def test_up_threshold_requires_two_success_cycles_before_recovery():
    """Recovery only after 2 consecutive successful checks when up_threshold=2."""
    db = make_db()
    store = make_db()

    # Create store with both wan and tunnel
    store = Store(store_code="70000123", pc_name="PC001", wan_dns="wan.example", ip_tunnel="10.0.0.1")
    db.add(store)
    db.commit()
    db.refresh(store)

    # First call: DOWN threshold=1 reached immediately
    update_status_and_incident(db, store, False, False, 1, 2)
    db.commit()
    assert store.status.overall_status == "DOWN"
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    incident.alert_sent = True
    db.commit()

    # First recovery check: still DOWN (need 2 consecutive successes)
    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, True, 1, 2)
    db.commit()
    assert status == "DOWN"  # not recovered yet, up_threshold=2 not reached
    assert recovered is False
    assert incident_ids == []
    assert db.query(Incident).filter(Incident.status == "OPEN").count() == 1

    # Second recovery check: now UP
    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, True, 1, 2)
    db.commit()
    assert status == "UP"
    assert recovered is True
    assert len(incident_ids) == 1
    assert db.query(Incident).filter(Incident.status == "OPEN").count() == 0
    resolved = db.query(Incident).filter(Incident.status == "RESOLVED").one()
    assert resolved.duration_seconds is not None


def test_recovery_does_not_notify_if_down_alert_was_never_sent():
    db = make_db()
    store = make_store(db)

    update_status_and_incident(db, store, False, False, 1, 2)
    db.commit()
    assert db.query(Incident).filter(Incident.status == "OPEN").count() == 1

    update_status_and_incident(db, store, True, True, 1, 2)
    db.commit()
    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, True, 1, 2)
    db.commit()

    assert status == "UP"
    assert recovered is True
    assert incident_ids == []
    assert db.query(Incident).filter(Incident.status == "RESOLVED").count() == 1



def test_recovery_works_when_only_wan_is_configured():
    """Store with only WAN configured recovers after 2 consecutive WAN UP."""
    db = make_db()
    store = Store(store_code="70000123", pc_name="PC-WAN", wan_dns="wan.example", ip_tunnel=None)
    db.add(store)
    db.commit()
    db.refresh(store)

    # First failure: immediately WAN_DOWN
    update_status_and_incident(db, store, False, None, 1, 2)
    db.commit()
    assert store.status.overall_status == "WAN_DOWN"
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    incident.alert_sent = True
    db.commit()

    # First recovery check: still WAN_DOWN (need 2 consecutive)
    update_status_and_incident(db, store, True, None, 1, 2)
    db.commit()
    assert store.status.overall_status == "WAN_DOWN"

    # Second recovery check: now UP
    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, True, None, 1, 2)
    db.commit()
    assert status == "UP"
    assert recovered is True
    assert incident_ids


def test_recovery_works_when_only_tunnel_is_configured():
    """Store with only tunnel configured recovers after 2 consecutive tunnel UP."""
    db = make_db()
    store = Store(store_code="70000123", pc_name="PC-TUN", wan_dns=None, ip_tunnel="10.0.0.1")
    db.add(store)
    db.commit()
    db.refresh(store)

    # First failure: immediately TUNNEL_DOWN
    update_status_and_incident(db, store, None, False, 1, 2)
    db.commit()
    assert store.status.overall_status == "TUNNEL_DOWN"
    incident = db.query(Incident).filter(Incident.status == "OPEN").one()
    incident.alert_sent = True
    db.commit()

    # First recovery check: still TUNNEL_DOWN
    update_status_and_incident(db, store, None, True, 1, 2)
    db.commit()
    assert store.status.overall_status == "TUNNEL_DOWN"

    # Second recovery check: now UP
    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, None, True, 1, 2)
    db.commit()
    assert status == "UP"
    assert recovered is True
    assert incident_ids


def test_no_recovery_when_no_targets_are_configured():
    """Store with no targets configured stays UNKNOWN."""
    db = make_db()
    store = Store(store_code="70000123", pc_name="PC-NONE", wan_dns=None, ip_tunnel=None)
    db.add(store)
    db.commit()
    db.refresh(store)

    changed, status, _old, recovered, incident_ids = update_status_and_incident(db, store, None, None, 1, 2)
    db.commit()
    assert status == "UNKNOWN"
    assert recovered is False
    assert incident_ids == []