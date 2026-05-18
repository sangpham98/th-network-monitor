import asyncio
import logging

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
    update_status_and_incident,
)

LOCK_PATH = settings.data_dir / "monitor.lock"
logger = logging.getLogger(__name__)


async def check_store(store: Store, semaphore: asyncio.Semaphore):
    async with semaphore:
        wan_ok = (
            await check_wan(store.wan_dns, settings.ping_timeout_seconds, settings.ping_retry)
            if store.wan_dns
            else None
        )
        tunnel_ok = (
            await ping_host(store.ip_tunnel, settings.ping_timeout_seconds, settings.ping_retry)
            if store.ip_tunnel
            else None
        )
        return store.id, wan_ok, tunnel_ok


def _mark_alert_sent(incident_ids: list[int], recovered: bool):
    if not incident_ids:
        return

    db = SessionLocal()
    try:
        for incident in db.query(Incident).filter(Incident.id.in_(incident_ids)).all():
            if recovered:
                incident.recovery_sent = True
            else:
                incident.alert_sent = True

            status = db.query(StoreStatus).filter(StoreStatus.store_id == incident.store_id).first()
            if status:
                status.last_alert_at = incident.ended_at if recovered else incident.started_at
        db.commit()
    finally:
        db.close()


def _build_alert_event(store: Store, status: str, recovered: bool, incident_ids: list[int]) -> dict:
    return {
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
    }


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
        _build_alert_event(store, incident.incident_type, False, [incident.id])
        for incident, store in query.order_by(Incident.started_at.asc()).all()
    ]


def build_telegram_batches(events: list[dict]) -> list[dict]:
    alert_events = [event for event in events if not event["recovered"]]
    recovery_events = [event for event in events if event["recovered"]]
    batches: list[dict] = []

    if 1 <= len(alert_events) <= 5:
        for event in alert_events:
            batches.append(
                {
                    "message": format_alert_event(event, recovered=False),
                    "incident_ids": event["incident_ids"],
                    "recovered": False,
                }
            )
    elif 6 <= len(alert_events) <= 30:
        batches.append(
            {
                "message": format_alert_summary(alert_events, recovered=False),
                "incident_ids": _flatten_incident_ids(alert_events),
                "recovered": False,
            }
        )
    elif len(alert_events) > 30:
        batches.append(
            {
                "message": format_major_incident(alert_events),
                "incident_ids": _flatten_incident_ids(alert_events),
                "recovered": False,
            }
        )

    if 1 <= len(recovery_events) <= 5:
        for event in recovery_events:
            batches.append(
                {
                    "message": format_alert_event(event, recovered=True),
                    "incident_ids": event["incident_ids"],
                    "recovered": True,
                }
            )
    elif len(recovery_events) > 5:
        batches.append(
            {
                "message": format_alert_summary(recovery_events, recovered=True),
                "incident_ids": _flatten_incident_ids(recovery_events),
                "recovered": True,
            }
        )

    return batches


async def _run_once_locked():
    db = SessionLocal()
    try:
        stores = db.query(Store).filter(Store.enabled.is_(True)).all()
        semaphore = asyncio.Semaphore(settings.max_concurrency)
        results = await asyncio.gather(*(check_store(store, semaphore) for store in stores))

        store_by_id = {store.id: store for store in stores}
        alert_events = []
        for store_id, wan_ok, tunnel_ok in results:
            store = store_by_id[store_id]
            changed, status, _old, recovered, incident_ids = update_status_and_incident(
                db=db,
                store=store,
                wan_ok=wan_ok,
                tunnel_ok=tunnel_ok,
                down_threshold=settings.down_threshold,
                up_threshold=settings.up_threshold,
            )
            if changed and incident_ids:
                alert_events.append(_build_alert_event(store, status, recovered, incident_ids))

        db.commit()
        if settings.telegram_bot_token and settings.telegram_chat_id:
            existing_incident_ids = set(_flatten_incident_ids(alert_events))
            alert_events.extend(_pending_open_alert_events(db, existing_incident_ids))
        telegram_batches = build_telegram_batches(alert_events)
        telegram_sent = 0
        telegram_failed = 0
        mark_failed = 0
        for batch in telegram_batches:
            incident_ids = batch["incident_ids"]
            recovered = batch["recovered"]
            sent = await send_telegram(batch["message"])
            if sent:
                telegram_sent += 1
                try:
                    _mark_alert_sent(incident_ids, recovered)
                except Exception:
                    mark_failed += 1
                    logger.exception(
                        "telegram sent but failed to mark incidents sent ids=%s recovered=%s",
                        incident_ids,
                        recovered,
                    )
            else:
                telegram_failed += 1
                logger.warning(
                    "telegram send failed; sent flags kept false ids=%s recovered=%s",
                    incident_ids,
                    recovered,
                )

        return {
            "status": "ok",
            "checked": len(stores),
            "alerts": len(alert_events),
            "messages": len(telegram_batches),
            "sent": telegram_sent,
            "send_failed": telegram_failed,
            "mark_failed": mark_failed,
        }
    finally:
        db.close()


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
        await asyncio.sleep(settings.monitor_interval_seconds)


if __name__ == "__main__":
    asyncio.run(run_forever())
