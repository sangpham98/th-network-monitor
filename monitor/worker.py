import asyncio
import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import Timeout, FileLock

from alerts.telegram import send_telegram
from app.config import settings
from app.database import SessionLocal, init_db
from app.logging_config import configure_logging
from app.models import Incident, Store, StoreStatus
from monitor.checker import check_wan, ping_host
from monitor.status_engine import format_current_incidents_summary, update_status_and_incident

LOCK_PATH = settings.data_dir / "monitor.lock"
STATUS_PATH = settings.data_dir / "monitor_status.json"
PING_PACKET_COUNT = 10
STORE_BATCH_SIZE = 50
TELEGRAM_SUMMARY_SLOTS = ("09:00", "14:00")
logger = logging.getLogger(__name__)
INVALID_TARGETS = {"0", "0.0.0.0", "-", "n/a", "na", "none", "null"}


def _target_or_none(value: str | None) -> str | None:
    target = (value or "").strip()
    return None if not target or target.lower() in INVALID_TARGETS else target


def _chunks(items: list[tuple[int, str | None, str | None]], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _write_monitor_status(payload: dict):
    existing = read_monitor_status()
    existing.update(payload)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATUS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(existing), encoding="utf-8")
    tmp_path.replace(STATUS_PATH)


def read_monitor_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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


def _local_now() -> datetime:
    try:
        timezone = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return datetime.now(timezone)


def _due_telegram_summary_slot(now: datetime | None = None) -> str | None:
    now = now or _local_now()
    sent_slots = read_monitor_status().get("telegram_summary_sent_slots") or {}
    for slot in TELEGRAM_SUMMARY_SLOTS:
        hour, minute = (int(part) for part in slot.split(":"))
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        slot_key = f"{now.date().isoformat()}T{slot}"
        if now >= slot_time and not sent_slots.get(slot_key):
            return slot
    return None


def _mark_telegram_summary_slot_sent(slot: str, now: datetime | None = None):
    now = now or _local_now()
    status = read_monitor_status()
    sent_slots = status.get("telegram_summary_sent_slots") or {}
    sent_slots[f"{now.date().isoformat()}T{slot}"] = True
    _write_monitor_status({"telegram_summary_sent_slots": sent_slots})


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


def _current_open_incident_events(db) -> list[dict]:
    query = (
        db.query(Incident, Store)
        .join(Store, Store.id == Incident.store_id)
        .filter(Incident.status == "OPEN", Store.enabled.is_(True))
        .order_by(Incident.started_at.asc(), Store.store_code.asc())
    )
    return [
        _build_alert_event(store, incident.incident_type, False, [incident.id], kind="summary", incident=incident)
        for incident, store in query.all()
    ]


def _apply_batch_results(results: list[tuple[int, bool | None, bool | None]]):
    db = SessionLocal()
    try:
        store_ids = [store_id for store_id, _wan_ok, _tunnel_ok in results]
        stores = db.query(Store).filter(Store.id.in_(store_ids), Store.enabled.is_(True)).all() if store_ids else []
        store_by_id = {store.id: store for store in stores}
        try:
            for store_id, wan_ok, tunnel_ok in results:
                store = store_by_id.get(store_id)
                if store is None:
                    continue
                update_status_and_incident(
                    db=db,
                    store=store,
                    wan_ok=wan_ok,
                    tunnel_ok=tunnel_ok,
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
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

    total_batches = (len(store_targets) + STORE_BATCH_SIZE - 1) // STORE_BATCH_SIZE
    _write_monitor_status(
        {
            "running": True,
            "batch_current": 0,
            "batch_total": total_batches,
            "checked": 0,
            "total": len(store_targets),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    checked = 0
    for batch_number, batch in enumerate(_chunks(store_targets, STORE_BATCH_SIZE), start=1):
        _write_monitor_status(
            {
                "running": True,
                "batch_current": batch_number,
                "batch_total": total_batches,
                "checked": checked,
                "total": len(store_targets),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        results = await asyncio.gather(
            *(check_store(store_id, wan_dns, ip_tunnel) for store_id, wan_dns, ip_tunnel in batch)
        )
        checked += len(results)
        _apply_batch_results(results)
        _write_monitor_status(
            {
                "running": True,
                "batch_current": batch_number,
                "batch_total": total_batches,
                "checked": checked,
                "total": len(store_targets),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    db = SessionLocal()
    try:
        summary_slot = _due_telegram_summary_slot() if settings.telegram_bot_token and settings.telegram_chat_id else None
        summary_events = []
        telegram_sent = 0
        telegram_failed = 0
        mark_failed = 0
        if summary_slot:
            summary_events = _current_open_incident_events(db)
            sent = await send_telegram(format_current_incidents_summary(summary_events, summary_slot))
            if sent:
                telegram_sent = 1
                try:
                    _mark_telegram_summary_slot_sent(summary_slot)
                except Exception:
                    mark_failed = 1
                    logger.exception("telegram summary sent but failed to mark slot sent slot=%s", summary_slot)
            else:
                telegram_failed = 1
                logger.warning("telegram summary send failed; slot kept pending slot=%s", summary_slot)

        result = {
            "status": "ok",
            "checked": checked,
            "alerts": len(summary_events),
            "messages": 1 if summary_slot else 0,
            "sent": telegram_sent,
            "send_failed": telegram_failed,
            "mark_failed": mark_failed,
            "summary_slot": summary_slot,
        }
        _write_monitor_status(
            {
                "running": False,
                "batch_current": total_batches,
                "batch_total": total_batches,
                "checked": checked,
                "total": len(store_targets),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        return result
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
