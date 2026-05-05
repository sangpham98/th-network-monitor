from datetime import datetime
from html import escape

from app.models import Incident, Store, StoreStatus


def _display(value) -> str:
    return escape(str(value)) if value else "-"


def _incident_text(event: dict) -> str:
    incident_ids = event.get("incident_ids") or []
    return ", ".join(str(incident_id) for incident_id in incident_ids) if incident_ids else "-"


def format_alert_event(event: dict, recovered: bool = False) -> str:
    title = "TH TRUEMART RECOVERY" if recovered else "TH TRUEMART ALERT"
    return "\n".join(
        [
            f"<b>{title}</b>",
            f"Store: <b>{_display(event.get('store_code'))}</b>",
            f"PC: {_display(event.get('pc_name'))}",
            f"Status: <b>{_display(event.get('status'))}</b>",
            f"WAN DNS: {_display(event.get('wan_dns'))}",
            f"Tunnel: {_display(event.get('ip_tunnel'))}",
            f"Miền: {_display(event.get('region'))}",
            f"Khu vực: {_display(event.get('area'))}",
            f"Địa chỉ: {_display(event.get('address'))}",
            f"Incident: {_display(_incident_text(event))}",
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
        lines.append(f"- {_display(value)}: {count}")


def format_alert_summary(events: list[dict], recovered: bool = False) -> str:
    title = "TH NETWORK RECOVERY SUMMARY" if recovered else "TH NETWORK ALERT SUMMARY"
    lines = [f"<b>{title}</b>", f"Tổng affected: {len(events)}", "", "Theo status:"]
    _append_counts(lines, _count_by(events, "status"))
    lines.extend(["", "Theo miền:"])
    _append_counts(lines, _count_by(events, "region"))
    lines.extend(["", "Theo khu vực:"])
    _append_counts(lines, _count_by(events, "area"))
    return "\n".join(lines)


def format_major_incident(events: list[dict]) -> str:
    lines = [
        "<b>TH NETWORK MAJOR INCIDENT</b>",
        f"Tổng affected: {len(events)}",
        "Gợi ý: kiểm tra hạ tầng WAN/VPN/DNS trung tâm.",
        "",
        "Theo status:",
    ]
    _append_counts(lines, _count_by(events, "status"))
    lines.extend(["", "Top khu vực:"])
    _append_counts(lines, _count_by(events, "area"), limit=10)
    lines.extend(["", "Stores: " + ", ".join(_display(event.get("store_code")) for event in events[:30])])
    if len(events) > 30:
        lines.append(f"...and {len(events) - 30} more")
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


def _set_target_counters(status: StoreStatus, wan_ok: bool | None, tunnel_ok: bool | None):
    status.wan_success_count = status.wan_success_count or 0
    status.wan_fail_count = status.wan_fail_count or 0
    status.tunnel_success_count = status.tunnel_success_count or 0
    status.tunnel_fail_count = status.tunnel_fail_count or 0

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


def _get_open_incident(db, store_id: int) -> Incident | None:
    return db.query(Incident).filter(Incident.store_id == store_id, Incident.status == "OPEN").order_by(Incident.started_at.desc()).first()


def _confirmed_recovery(store: Store, status: StoreStatus, wan_ok: bool | None, tunnel_ok: bool | None, up_threshold: int) -> bool:
    wan_required = bool(store.wan_dns)
    tunnel_required = bool(store.ip_tunnel)

    if not wan_required and not tunnel_required:
        return False

    wan_recovered = not wan_required or (wan_ok is True and status.wan_success_count >= up_threshold)
    tunnel_recovered = not tunnel_required or (tunnel_ok is True and status.tunnel_success_count >= up_threshold)
    return wan_recovered and tunnel_recovered


def update_status_and_incident(
    db,
    store: Store,
    wan_ok: bool | None,
    tunnel_ok: bool | None,
    down_threshold: int,
    up_threshold: int,
):
    now = datetime.utcnow()
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
    confirmed_up = _confirmed_recovery(store, status, wan_ok, tunnel_ok, up_threshold)
    effective_overall = "UP" if new_overall == "UP" and confirmed_up else new_overall
    changed = old_overall != effective_overall
    if changed and effective_overall != "UP":
        status.overall_status = effective_overall
        status.last_changed_at = now

    confirmed_down = (
        effective_overall != "UP"
        and (status.wan_fail_count >= down_threshold or status.tunnel_fail_count >= down_threshold)
    )
    recovered = False
    incident_ids: list[int] = []

    if confirmed_down:
        open_incident = _get_open_incident(db, store.id)
        if open_incident is None:
            open_incident = Incident(
                store_id=store.id,
                incident_type=effective_overall,
                status="OPEN",
                detail=f"Changed from {old_overall} to {effective_overall}",
            )
            db.add(open_incident)
            db.flush()
            changed = True
        elif open_incident.incident_type != effective_overall or old_overall != effective_overall:
            open_incident.incident_type = effective_overall
            open_incident.detail = f"Changed from {old_overall} to {effective_overall}"
            changed = True

        if changed and not open_incident.alert_sent:
            incident_ids.append(open_incident.id)

    elif confirmed_up and old_overall != "UP":
        status.overall_status = "UP"
        status.last_changed_at = now
        open_incidents = db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").all()
        for incident in open_incidents:
            incident.status = "RESOLVED"
            incident.ended_at = now
            incident.duration_seconds = int((now - incident.started_at).total_seconds())
            if not incident.recovery_sent:
                incident_ids.append(incident.id)
        changed = True
        recovered = True

    status.last_check_at = now
    db.add(status)
    return changed, status.overall_status, old_overall, recovered, incident_ids