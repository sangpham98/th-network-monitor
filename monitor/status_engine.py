from datetime import UTC, datetime
from html import escape

from app.models import Incident, Store, StoreStatus


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _display(value) -> str:
    return escape(str(value)) if value else "-"


def _incident_text(event: dict) -> str:
    incident_ids = event.get("incident_ids") or []
    return ", ".join(str(incident_id) for incident_id in incident_ids) if incident_ids else "-"


def format_alert_event(event: dict, recovered: bool = False) -> str:
    title = "✅ TH TRUEMART RECOVERY" if recovered else "🚨 TH TRUEMART ALERT"
    return "\n".join(
        [
            f"<b>{title}</b>",
            f"🏪 Store: <b>{_display(event.get('store_code'))}</b>",
            f"💻 PC: {_display(event.get('pc_name'))}",
            f"📊 Status: <b>{_display(event.get('status'))}</b>",
            f"🌐 WAN DNS: {_display(event.get('wan_dns'))}",
            f"🔗 Tunnel: {_display(event.get('ip_tunnel'))}",
            f"🌏 Miền: {_display(event.get('region'))}",
            f"🗺️ Khu vực: {_display(event.get('area'))}",
            f"📍 Địa chỉ: {_display(event.get('address'))}",
            f"🆔 Incident: {_display(_incident_text(event))}",
        ]
    )


def format_reminder_event(event: dict) -> str:
    return "\n".join(
        [
            "<b>🔔 TH TRUEMART REMINDER</b>",
            f"🏪 Store: <b>{_display(event.get('store_code'))}</b>",
            f"💻 PC: {_display(event.get('pc_name'))}",
            f"📊 Status: <b>{_display(event.get('status'))}</b>",
            f"🌐 WAN DNS: {_display(event.get('wan_dns'))}",
            f"🔗 Tunnel: {_display(event.get('ip_tunnel'))}",
            f"🌏 Miền: {_display(event.get('region'))}",
            f"🗺️ Khu vực: {_display(event.get('area'))}",
            f"📍 Địa chỉ: {_display(event.get('address'))}",
            f"🆔 Incident: {_display(_incident_text(event))}",
            f"🕒 Started: {_display(event.get('started_at'))}",
            f"🔁 Reminder count: {_display(event.get('reminder_count'))}",
        ]
    )


def _count_by(events: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        value = event.get(key) or "-"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _append_counts(lines: list[str], counts: dict[str, int], limit: int | None = None):
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None:
        items = items[:limit]
    for value, count in items:
        lines.append(f"• {_display(value)}: <b>{count}</b>")


def format_alert_summary(events: list[dict], recovered: bool = False) -> str:
    title = "✅ TH NETWORK RECOVERY SUMMARY" if recovered else "🚨 TH NETWORK ALERT SUMMARY"
    lines = [f"<b>{title}</b>", f"📌 Tổng affected: <b>{len(events)}</b>", "", "📊 Theo status:"]
    _append_counts(lines, _count_by(events, "status"))
    lines.extend(["", "🌏 Theo miền:"])
    _append_counts(lines, _count_by(events, "region"))
    lines.extend(["", "🗺️ Theo khu vực:"])
    _append_counts(lines, _count_by(events, "area"))
    return "\n".join(lines)


def format_reminder_summary(events: list[dict]) -> str:
    lines = ["<b>🔔 TH NETWORK REMINDER SUMMARY</b>", f"📌 Tổng unresolved: <b>{len(events)}</b>", "", "📊 Theo status:"]
    _append_counts(lines, _count_by(events, "status"))
    lines.extend(["", "🌏 Theo miền:"])
    _append_counts(lines, _count_by(events, "region"))
    lines.extend(["", "🗺️ Theo khu vực:"])
    _append_counts(lines, _count_by(events, "area"))
    return "\n".join(lines)


def format_major_incident(events: list[dict]) -> str:
    lines = [
        "<b>🔥 TH NETWORK MAJOR INCIDENT</b>",
        f"📌 Tổng affected: <b>{len(events)}</b>",
        "🛠️ Gợi ý: kiểm tra hạ tầng WAN/VPN/DNS trung tâm.",
        "",
        "📊 Theo status:",
    ]
    _append_counts(lines, _count_by(events, "status"))
    lines.extend(["", "🗺️ Top khu vực:"])
    _append_counts(lines, _count_by(events, "area"), limit=10)
    lines.extend(["", "🏪 Stores: " + ", ".join(_display(event.get("store_code")) for event in events[:30])])
    if len(events) > 30:
        lines.append(f"…and {len(events) - 30} more")
    return "\n".join(lines)


def derive_overall(wan_ok: bool | None, tunnel_ok: bool | None) -> str:
    if wan_ok is True and tunnel_ok is True:
        return "UP"
    if wan_ok is False and tunnel_ok is True:
        return "WAN_DOWN"
    if wan_ok is True and tunnel_ok is False:
        return "TUNNEL_DOWN"
    if wan_ok is False and tunnel_ok is False:
        return "DOWN"
    return "UNKNOWN"


def derive_store_overall(store: Store, wan_ok: bool | None, tunnel_ok: bool | None) -> str:
    wan_required = bool(store.wan_dns)
    tunnel_required = bool(store.ip_tunnel)

    if wan_required and tunnel_required:
        return derive_overall(wan_ok, tunnel_ok)
    if wan_required and not tunnel_required:
        if wan_ok is True:
            return "UP"
        if wan_ok is False:
            return "WAN_DOWN"
    if tunnel_required and not wan_required:
        if tunnel_ok is True:
            return "UP"
        if tunnel_ok is False:
            return "TUNNEL_DOWN"
    return "UNKNOWN"


DOWN_WINDOW_SIZE = 5


def _clean_down_window(value: str | None) -> str:
    return "".join(char for char in (value or "") if char in {"0", "1"})[-DOWN_WINDOW_SIZE:]


def _append_down_window(value: str | None, ok: bool | None) -> str:
    window = _clean_down_window(value)
    if ok is True:
        window += "0"
    elif ok is False:
        window += "1"
    return window[-DOWN_WINDOW_SIZE:]


def _set_target_counters(status: StoreStatus, wan_ok: bool | None, tunnel_ok: bool | None):
    status.wan_success_count = status.wan_success_count or 0
    status.wan_fail_count = status.wan_fail_count or 0
    status.tunnel_success_count = status.tunnel_success_count or 0
    status.tunnel_fail_count = status.tunnel_fail_count or 0
    status.wan_down_window = _append_down_window(status.wan_down_window, wan_ok)
    status.tunnel_down_window = _append_down_window(status.tunnel_down_window, tunnel_ok)

    if wan_ok is True:
        status.wan_success_count += 1
        status.wan_fail_count = 0
    elif wan_ok is False:
        status.wan_fail_count += 1
        status.wan_success_count = 0

    if tunnel_ok is True:
        status.tunnel_success_count += 1
        status.tunnel_fail_count = 0
    elif tunnel_ok is False:
        status.tunnel_fail_count += 1
        status.tunnel_success_count = 0


def _down_confirmed(status: StoreStatus, overall: str) -> bool:
    if overall == "WAN_DOWN":
        return (status.wan_fail_count or 0) >= 2
    if overall == "TUNNEL_DOWN":
        return (status.tunnel_fail_count or 0) >= 2
    if overall == "DOWN":
        return (status.wan_fail_count or 0) >= 2 and (status.tunnel_fail_count or 0) >= 2
    return False


def _get_open_incident(db, store_id: int) -> Incident | None:
    return db.query(Incident).filter(Incident.store_id == store_id, Incident.status == "OPEN").order_by(Incident.started_at.desc()).first()


def update_status_and_incident(
    db,
    store: Store,
    wan_ok: bool | None,
    tunnel_ok: bool | None,
):
    now = utc_now()
    status = store.status
    if status is None:
        status = StoreStatus(store_id=store.id)
        store.status = status
    status.overall_status = status.overall_status or "UNKNOWN"
    status.wan_status = status.wan_status or "UNKNOWN"
    status.tunnel_status = status.tunnel_status or "UNKNOWN"
    old_overall = status.overall_status

    status.wan_status = "UP" if wan_ok else "DOWN" if wan_ok is False else "UNKNOWN"
    status.tunnel_status = "UP" if tunnel_ok else "DOWN" if tunnel_ok is False else "UNKNOWN"
    _set_target_counters(status, wan_ok, tunnel_ok)

    new_overall = derive_store_overall(store, wan_ok, tunnel_ok)
    changed = False
    recovered = False
    incident_ids: list[int] = []

    if new_overall in {"WAN_DOWN", "TUNNEL_DOWN", "DOWN"}:
        if old_overall != new_overall:
            status.overall_status = new_overall
            status.last_changed_at = now
            changed = True

        if _down_confirmed(status, new_overall):
            open_incident = _get_open_incident(db, store.id)
            if open_incident is None:
                open_incident = Incident(
                    store_id=store.id,
                    incident_type=new_overall,
                    status="OPEN",
                    detail=f"Changed from {old_overall} to {new_overall}",
                )
                db.add(open_incident)
                db.flush()
                changed = True
            elif open_incident.incident_type != new_overall or old_overall != new_overall:
                open_incident.incident_type = new_overall
                open_incident.detail = f"Changed from {old_overall} to {new_overall}"
                changed = True

            if changed and not open_incident.alert_sent:
                incident_ids.append(open_incident.id)

    elif new_overall == "UP" and old_overall != "UP":
        status.overall_status = "UP"
        status.last_changed_at = now
        open_incidents = db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").all()
        for incident in open_incidents:
            incident.status = "RESOLVED"
            incident.ended_at = now
            incident.duration_seconds = int((now - incident.started_at).total_seconds())
            if incident.alert_sent and not incident.recovery_sent:
                incident_ids.append(incident.id)
        changed = True
        recovered = True
    elif new_overall == "UNKNOWN" and old_overall != "UNKNOWN":
        status.overall_status = "UNKNOWN"
        status.last_changed_at = now
        changed = True

    status.last_check_at = now
    db.add(status)
    return changed, status.overall_status, old_overall, recovered, incident_ids