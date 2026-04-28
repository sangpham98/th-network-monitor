from datetime import datetime

from app.models import Incident, Store, StoreStatus


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


def format_alert(store: Store, status: str, recovered: bool = False) -> str:
    icon = "🟢" if recovered else "🔴"
    title = "TH TRUEMART RECOVERY" if recovered else "TH TRUEMART ALERT"
    return (
        f"{icon} <b>{title}</b>\n\n"
        f"Mã CH: <b>{store.store_code}</b>\n"
        f"PC Name: {store.pc_name or '-'}\n"
        f"Miền/Khu vực: {store.region or '-'} / {store.area or '-'}\n"
        f"Địa chỉ: {store.address or '-'}\n"
        f"Trạng thái: <b>{status}</b>\n"
        f"WAN/DNS: {store.wan_dns or '-'}\n"
        f"IP Tunnel: {store.ip_tunnel or '-'}\n"
        f"Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def update_status_and_incident(db, store: Store, wan_ok: bool | None, tunnel_ok: bool | None, down_threshold: int):
    now = datetime.utcnow()
    status = store.status or StoreStatus(store_id=store.id)
    old_overall = status.overall_status

    status.wan_status = "UP" if wan_ok else "DOWN" if wan_ok is False else "UNKNOWN"
    status.tunnel_status = "UP" if tunnel_ok else "DOWN" if tunnel_ok is False else "UNKNOWN"
    status.wan_fail_count = 0 if wan_ok else status.wan_fail_count + 1
    status.tunnel_fail_count = 0 if tunnel_ok else status.tunnel_fail_count + 1

    new_overall = derive_overall(wan_ok, tunnel_ok)
    confirmed_down = status.wan_fail_count >= down_threshold or status.tunnel_fail_count >= down_threshold
    confirmed_up = bool(wan_ok and tunnel_ok)
    changed = False
    recovered = False

    if confirmed_down and new_overall != old_overall:
        status.overall_status = new_overall
        status.last_changed_at = now
        db.add(
            Incident(
                store_id=store.id,
                incident_type=new_overall,
                status="OPEN",
                detail=f"Changed from {old_overall} to {new_overall}",
            )
        )
        changed = True
    elif confirmed_up and old_overall != "UP":
        status.overall_status = "UP"
        status.last_changed_at = now
        for incident in db.query(Incident).filter(Incident.store_id == store.id, Incident.status == "OPEN").all():
            incident.status = "RESOLVED"
            incident.ended_at = now
            incident.duration_seconds = int((now - incident.started_at).total_seconds())
        changed = True
        recovered = True

    status.last_check_at = now
    db.add(status)
    return changed, status.overall_status, old_overall, recovered
