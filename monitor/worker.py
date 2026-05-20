import asyncio
import logging
from datetime import UTC, datetime, timedelta

from filelock import Timeout, FileLock
from sqlalchemy import or_

from alerts.telegram import send_telegram
from app.config import settings
from app.database import SessionLocal, init_db
from app.logging_config import configure_logging
from app.models import Incident, Store, StoreStatus
from monitor.checker import check_wan, ping_host
from monitor.status_engine import (
    format_alert_event,
    format_alert_summary,
    format_major_incident,
    format_reminder_event,
    format_reminder_summary,
    update_status_and_incident,
)

LOCK_PATH = settings.data_dir / "monitor.lock"
PING_PACKET_COUNT = 5
STORE_BATCH_SIZE = 50
logger = logging.getLogger(__name__)
INVALID_TARGETS = {"0", "0.0.0.0", "-", "n/a", "na", "none", "null"}


def _target_or_none(value: str | None) -> str | None:
    target = (value or "").strip()
    return None if not target or target.lower() in INVALID_TARGETS else target


def _chunks(items: list[tuple[int, str | None, str | None]], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


async def check_store(store_id: int, wan_dns: str | None, ip_tunnel: str | None):
    wan_target = _target_or_none(wan_dns)
    tunnel_target = _target_or_none(ip_tunnel)
    wan_ok = (
        await check_wan(wan_target, settings.ping_timeout_seconds, PING_PACKET_COUNT)
        if wan_target
        else None
    )
    tunnel_ok = (
        await ping_host(tunnel_target, settings.ping_timeout_seconds, PING_PACKET_COUNT)
        if tunnel_target
        else None
    )
    return store_id, wan_ok, tunnel_ok


def _mark_notification_sent(incident_ids: list[int], kind: str):
    if not incident_ids:
        return

    sent_at = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    try:
        for incident in db.query(Incident).filter(Incident.id.in_(incident_ids)).all():
            if kind == "recovery":
                incident.recovery_sent = True
                last_alert_at = incident.ended_at
            elif kind == "reminder":
                incident.last_reminder_at = sent_at
                incident.reminder_count = (incident.reminder_count or 0) + 1
                last_alert_at = sent_at
            else:
                incident.alert_sent = True
                incident.alert_sent_at = sent_at
                last_alert_at = incident.started_at

            status = db.query(StoreStatus).filter(StoreStatus.store_id == incident.store_id).first()
            if status:
                status.last_alert_at = last_alert_at
        db.commit()
    finally:
        db.close()


def _build_alert_event(
    store: Store,
    status: str,
    recovered: bool,
    incident_ids: list[int],
    kind: str | None = None,
    incident: Incident | None = None,
) -> dict:
    event = {
        "store_id": store.id,
        "store_code": store.store_code,
        "pc_name": store.pc_name,
        "region": store.region,
        "area": store.area,
        "address": store.address,
        "status": status,
        "wan_dns": store.wan_dns,
        "ip_tunnel": store.ip_tunnel,
        "incident_ids": incident_ids,
        "recovered": recovered,
        "kind": kind or ("recovery" if recovered else "alert"),
    }
    if incident is not None:
        event["started_at"] = incident.started_at
        event["reminder_count"] = incident.reminder_count or 0
    return event


def _flatten_incident_ids(events: list[dict]) -> list[int]:
    incident_ids: list[int] = []
    for event in events:
        incident_ids.extend(event["incident_ids"])
    return incident_ids


def _pending_open_alert_events(db, excluded_incident_ids: set[int]) -> list[dict]:
    query = (
        db.query(Incident, Store)
        .join(Store, Store.id == Incident.store_id)
        .filter(
            Incident.status == "OPEN",
            or_(Incident.alert_sent.is_(False), Incident.alert_sent.is_(None)),
            Store.enabled.is_(True),
        )
    )
    if excluded_incident_ids:
        query = query.filter(Incident.id.notin_(excluded_incident_ids))

    return [
        _build_alert_event(store, incident.incident_type, False, [incident.id], kind="alert", incident=incident)
        for incident, store in query.order_by(Incident.started_at.asc()).all()
    ]


def _pending_reminder_events(db, excluded_incident_ids: set[int], now: datetime) -> list[dict]:
    interval_seconds = settings.telegram_reminder_interval_seconds
    if interval_seconds <= 0:
        return []

    due_before = now - timedelta(seconds=interval_seconds)
    query = (
        db.query(Incident, Store)
        .join(Store, Store.id == Incident.store_id)
        .filter(
            Incident.status == "OPEN",
            Incident.alert_sent.is_(True),
            Store.enabled.is_(True),
        )
    )
    if excluded_incident_ids:
        query = query.filter(Incident.id.notin_(excluded_incident_ids))

    events = []
    for incident, store in query.order_by(Incident.started_at.asc()).all():
        anchor = incident.last_reminder_at or incident.alert_sent_at
        if anchor is None or anchor > due_before:
            continue
        events.append(_build_alert_event(store, incident.incident_type, False, [incident.id], kind="reminder", incident=incident))
    return events


def build_telegram_batches(events: list[dict]) -> list[dict]:
    alert_events = [event for event in events if event.get("kind") == "alert" or (not event.get("kind") and not event["recovered"])]
    reminder_events = [event for event in events if event.get("kind") == "reminder"]
    recovery_events = [event for event in events if event.get("kind") == "recovery" or (not event.get("kind") and event["recovered"])]
    batches: list[dict] = []

    if 1 <= len(alert_events) <= 5:
        for event in alert_events:
            batches.append(
                {
                    "message": format_alert_event(event, recovered=False),
                    "incident_ids": event["incident_ids"],
                    "kind": "alert",
                    "recovered": False,
                }
            )
    elif 6 <= len(alert_events) <= 30:
        batches.append(
            {
                "message": format_alert_summary(alert_events, recovered=False),
                "incident_ids": _flatten_incident_ids(alert_events),
                "kind": "alert",
                "recovered": False,
            }
        )
    elif len(alert_events) > 30:
        batches.append(
            {
                "message": format_major_incident(alert_events),
                "incident_ids": _flatten_incident_ids(alert_events),
                "kind": "alert",
                "recovered": False,
            }
        )

    if 1 <= len(reminder_events) <= 5:
        for event in reminder_events:
            batches.append(
                {
                    "message": format_reminder_event(event),
                    "incident_ids": event["incident_ids"],
                    "kind": "reminder",
                    "recovered": False,
                }
            )
    elif len(reminder_events) > 5:
        batches.append(
            {
                "message": format_reminder_summary(reminder_events),
                "incident_ids": _flatten_incident_ids(reminder_events),
                "kind": "reminder",
                "recovered": False,
            }
        )

    if 1 <= len(recovery_events) <= 5:
        for event in recovery_events:
            batches.append(
                {
                    "message": format_alert_event(event, recovered=True),
                    "incident_ids": event["incident_ids"],
                    "kind": "recovery",
                    "recovered": True,
                }
            )
    elif len(recovery_events) > 5:
        batches.append(
            {
                "message": format_alert_summary(recovery_events, recovered=True),
                "incident_ids": _flatten_incident_ids(recovery_events),
                "kind": "recovery",
                "recovered": True,
            }
        )

    return batches


def _apply_batch_results(results: list[tuple[int, bool | None, bool | None]]) -> list[dict]:
    db = SessionLocal()
    try:
        store_ids = [store_id for store_id, _wan_ok, _tunnel_ok in results]
        stores = db.query(Store).filter(Store.id.in_(store_ids), Store.enabled.is_(True)).all() if store_ids else []
        store_by_id = {store.id: store for store in stores}
        alert_events = []
        try:
            for store_id, wan_ok, tunnel_ok in results:
                store = store_by_id.get(store_id)
                if store is None:
                    continue
                changed, status, _old, recovered, incident_ids = update_status_and_incident(
                    db=db,
                    store=store,
                    wan_ok=wan_ok,
                    tunnel_ok=tunnel_ok,
                )
                if changed and incident_ids:
                    alert_events.append(_build_alert_event(store, status, recovered, incident_ids))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return alert_events
    finally:
        db.close()


async def _run_once_locked():
    db = SessionLocal()
    try:
        store_targets = [
            (store_id, wan_dns, ip_tunnel)
            for store_id, wan_dns, ip_tunnel in db.query(Store.id, Store.wan_dns, Store.ip_tunnel)
            .filter(Store.enabled.is_(True))
            .order_by(Store.id)
            .all()
        ]
    finally:
        db.close()

    alert_events = []
    checked = 0
    for batch in _chunks(store_targets, STORE_BATCH_SIZE):
        results = await asyncio.gather(
            *(check_store(store_id, wan_dns, ip_tunnel) for store_id, wan_dns, ip_tunnel in batch)
        )
        checked += len(results)
        alert_events.extend(_apply_batch_results(results))

    db = SessionLocal()
    try:
        if settings.telegram_bot_token and settings.telegram_chat_id:
            existing_incident_ids = set(_flatten_incident_ids(alert_events))
            alert_events.extend(_pending_open_alert_events(db, existing_incident_ids))
            existing_incident_ids = set(_flatten_incident_ids(alert_events))
            alert_events.extend(_pending_reminder_events(db, existing_incident_ids, datetime.now(UTC).replace(tzinfo=None)))
        telegram_batches = build_telegram_batches(alert_events)
        telegram_sent = 0
        telegram_failed = 0
        mark_failed = 0
        for batch in telegram_batches:
            incident_ids = batch["incident_ids"]
            kind = batch["kind"]
            sent = await send_telegram(batch["message"])
            if sent:
                telegram_sent += 1
                try:
                    _mark_notification_sent(incident_ids, kind)
                except Exception:
                    mark_failed += 1
                    logger.exception(
                        "telegram sent but failed to mark notification sent ids=%s kind=%s",
                        incident_ids,
                        kind,
                    )
            else:
                telegram_failed += 1
                logger.warning(
                    "telegram send failed; sent flags kept false ids=%s kind=%s",
                    incident_ids,
                    kind,
                )

        return {
            "status": "ok",
            "checked": checked,
            "alerts": len(alert_events),
            "messages": len(telegram_batches),
            "sent": telegram_sent,
            "send_failed": telegram_failed,
            "mark_failed": mark_failed,
        }
    finally:
        db.close()


def monitor_is_running() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH), timeout=0)
    try:
        with lock:
            return False
    except Timeout:
        return True


async def run_once():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH), timeout=0)
    try:
        with lock:
            return await _run_once_locked()
    except Timeout:
        return {"status": "skipped", "reason": "monitor already running"}


async def run_forever():
    configure_logging()
    init_db()
    while True:
        try:
            result = await run_once()
            if result.get("status") == "skipped":
                logger.info("monitor skipped: %s", result["reason"])
            else:
                logger.info(
                    "monitor checked=%s alerts=%s messages=%s sent=%s send_failed=%s mark_failed=%s",
                    result["checked"],
                    result["alerts"],
                    result["messages"],
                    result["sent"],
                    result.get("send_failed", 0),
                    result.get("mark_failed", 0),
                )
        except Exception:
            logger.exception("monitor error")


if __name__ == "__main__":
    asyncio.run(run_forever())
