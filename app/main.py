import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alerts.telegram import send_telegram
from app.backups import create_sqlite_backup, list_backups, resolve_backup, restore_sqlite_backup, sqlite_db_path
from app.auth import auth_configured, clear_login_cookie, require_auth, set_login_cookie
from app.config import DEFAULT_TIMEZONE, settings
from app.database import get_db, init_db
from app.logging_config import configure_logging
from app.models import Incident, Store, StoreStatus
from app.reports import build_incident_report, build_store_report
from app.store_utils import (
    IP_FIELDS,
    STORE_EXCEL_COLUMNS,
    STORE_EXCEL_HEADERS,
    STORE_FORM_FIELDS,
    clean_store_value,
    ensure_store_status,
    set_store_optional_fields,
    valid_ip,
    valid_store_code_format,
)
from importers.excel_importer import import_excel, preview_excel
from monitor.worker import monitor_is_running, read_monitor_status, run_once

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = settings.data_dir / "uploads"
PREVIEW_DIR = settings.data_dir / "import_previews"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title="TH Network Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")


@lru_cache(maxsize=8)
def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_timezone(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")


def _display_target_status(store: Store, target: str) -> str:
    status = store.status
    if status is None:
        return "UNKNOWN"
    if target == "wan":
        if not store.wan_dns:
            return "UNKNOWN"
        return status.wan_status or "UNKNOWN"
    if not store.ip_tunnel:
        return "UNKNOWN"
    return status.tunnel_status or "UNKNOWN"


def display_wan_status(store: Store) -> str:
    return _display_target_status(store, "wan")


def display_tunnel_status(store: Store) -> str:
    return _display_target_status(store, "tunnel")


def display_overall_status(store: Store) -> str:
    if store.status is None:
        return "UNKNOWN"
    return store.status.overall_status or "UNKNOWN"


def monitor_context(db: Session) -> dict:
    latest = db.query(StoreStatus.last_check_at).order_by(StoreStatus.last_check_at.desc()).first()
    return {
        "monitor_running": monitor_is_running(),
        "monitor_status": read_monitor_status(),
        "latest_check_at": latest[0] if latest else None,
        "auto_refresh_seconds": settings.monitor_interval_seconds,
    }


DASHBOARD_QUICK_RANGES = {
    "30m": ("30 phút", timedelta(minutes=30)),
    "1h": ("1 hour", timedelta(hours=1)),
    "12h": ("12 hours", timedelta(hours=12)),
    "1d": ("1 day", timedelta(days=1)),
    "7d": ("7 day", timedelta(days=7)),
    "1w": ("1 week", timedelta(weeks=1)),
    "1mo": ("1 month", timedelta(days=30)),
}
DASHBOARD_INCIDENT_TYPES = ("DOWN", "TUNNEL_DOWN", "WAN_DOWN")


def _parse_datetime_local(value: str, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def timeline_ticks(range_start: datetime, range_end: datetime) -> list[str]:
    total_seconds = (range_end - range_start).total_seconds()
    if total_seconds <= 3600:
        step = timedelta(minutes=5 if total_seconds <= 1800 else 10)
        fmt = "%H:%M"
    elif total_seconds <= 12 * 3600:
        step = timedelta(hours=1)
        fmt = "%H:%M"
    elif total_seconds <= 24 * 3600:
        step = timedelta(hours=3)
        fmt = "%H:%M"
    elif total_seconds <= 7 * 24 * 3600:
        step = timedelta(hours=12)
        fmt = "%m-%d %H:%M"
    else:
        step = timedelta(days=2)
        fmt = "%m-%d %H:%M"

    ticks = []
    current = range_start
    while current < range_end and len(ticks) < 16:
        ticks.append(current.strftime(fmt))
        current += step
    end_label = range_end.strftime(fmt)
    if not ticks or ticks[-1] != end_label:
        ticks.append(end_label)
    return ticks


def dashboard_time_filter(
    time_mode: str = "quick",
    quick_range: str = "1d",
    start: str = "",
    end: str = "",
    now: datetime | None = None,
) -> dict:
    timezone = _timezone(settings.timezone)
    local_now = now or datetime.now(timezone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=timezone)
    local_now = local_now.astimezone(timezone)
    selected_range = quick_range if quick_range in DASHBOARD_QUICK_RANGES else "1d"
    mode = "absolute" if time_mode == "absolute" else "quick"

    absolute_start = _parse_datetime_local(start, timezone)
    absolute_end = _parse_datetime_local(end, timezone)
    if mode == "absolute" and absolute_start and absolute_end and absolute_end > absolute_start:
        range_start = absolute_start
        range_end = absolute_end
    else:
        mode = "quick"
        range_end = local_now
        range_start = local_now - DASHBOARD_QUICK_RANGES[selected_range][1]

    ticks = timeline_ticks(range_start, range_end)
    return {
        "mode": mode,
        "quick_range": selected_range,
        "start": range_start,
        "end": range_end,
        "start_value": range_start.strftime("%Y-%m-%dT%H:%M"),
        "end_value": range_end.strftime("%Y-%m-%dT%H:%M"),
        "label": f"{range_start.strftime('%Y-%m-%d %H:%M')} → {range_end.strftime('%Y-%m-%d %H:%M')}",
        "ticks": ticks,
        "start_utc": range_start.astimezone(UTC).replace(tzinfo=None),
        "end_utc": range_end.astimezone(UTC).replace(tzinfo=None),
    }


def dashboard_incident_stores(db: Session, incident_type: str, range_start: datetime, range_end: datetime) -> list[Store]:
    rows = (
        db.query(Store)
        .join(Incident, Incident.store_id == Store.id)
        .filter(
            Incident.incident_type == incident_type,
            Incident.started_at < range_end,
            or_(Incident.ended_at.is_(None), Incident.ended_at > range_start),
        )
        .order_by(Store.store_code)
        .all()
    )
    seen = set()
    stores = []
    for store in rows:
        if store.id in seen:
            continue
        seen.add(store.id)
        stores.append(store)
    return stores


def build_dashboard_incident_sections(db: Session, time_filter: dict) -> list[dict]:
    return [
        {
            "type": incident_type,
            "title": f"Store {incident_type}",
            "stores": dashboard_incident_stores(db, incident_type, time_filter["start_utc"], time_filter["end_utc"]),
        }
        for incident_type in DASHBOARD_INCIDENT_TYPES
    ]


def _safe_redirect_path(value: str) -> str:
    if value.startswith("/") and not value.startswith("//") and "\r" not in value and "\n" not in value:
        return value
    return "/"


def _store_form_data(values: dict | Store | None = None, enabled: bool = True) -> dict:
    if values is None:
        values = {}
    data = {field: clean_store_value(getattr(values, field, None) if isinstance(values, Store) else values.get(field)) for field in STORE_FORM_FIELDS}
    data["enabled"] = bool(getattr(values, "enabled", enabled) if isinstance(values, Store) else enabled)
    return data


def _validate_store_form(db: Session, data: dict, store_id: int | None = None) -> list[str]:
    errors = []
    store_code = data.get("store_code")
    if store_id is None:
        if not valid_store_code_format(store_code):
            errors.append("Mã CH không đúng định dạng (cần 7 hoặc 8 số, bắt đầu bằng 70000).")
        elif db.query(Store.id).filter(Store.store_code == store_code).first() is not None:
            errors.append("Mã CH đã tồn tại.")

    for field in IP_FIELDS:
        if not valid_ip(data.get(field)):
            errors.append(f"{field} không hợp lệ.")
    return errors


def _store_form_context(current_user: str, data: dict, errors: list[str], store: Store | None = None) -> dict:
    if store:
        return {
            "store": store,
            "form": data,
            "errors": errors,
            "current_user": current_user,
            "title": f"Sửa store {data['store_code']}",
            "form_action": f"/stores/{store.id}/edit",
            "cancel_url": f"/stores/{store.id}",
            "store_code_readonly": True,
        }
    return {
        "store": None,
        "form": data,
        "errors": errors,
        "current_user": current_user,
        "title": "Thêm store",
        "form_action": "/stores",
        "cancel_url": "/stores",
        "store_code_readonly": False,
    }


templates.env.filters["local_datetime"] = local_datetime
templates.env.globals["display_wan_status"] = display_wan_status
templates.env.globals["display_tunnel_status"] = display_tunnel_status
templates.env.globals["display_overall_status"] = display_overall_status


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "", "auth_configured": auth_configured()},
    )


@app.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form("")):
    from app.auth import credentials_valid

    if not credentials_valid(username, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Sai thông tin đăng nhập hoặc auth chưa được cấu hình an toàn.",
                "auth_configured": auth_configured(),
            },
            status_code=401,
        )

    response = RedirectResponse(url="/", status_code=303)
    set_login_cookie(response, username)
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    clear_login_cookie(response)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    time_mode: str = "quick",
    quick_range: str = "1d",
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
    current_user: str = Depends(require_auth),
):
    total = db.query(Store).count()
    all_stores = db.query(Store).outerjoin(StoreStatus).order_by(Store.store_code).all()
    status_counts: dict[str, int] = {}
    for store in all_stores:
        display_status = display_overall_status(store)
        status_counts[display_status] = status_counts.get(display_status, 0) + 1
    time_filter = dashboard_time_filter(time_mode, quick_range, start, end)
    incident_sections = build_dashboard_incident_sections(db, time_filter)
    timeline_stores = []
    seen_store_ids = set()
    for section in incident_sections:
        for store in section["stores"]:
            if store.id in seen_store_ids:
                continue
            seen_store_ids.add(store.id)
            timeline_stores.append(store)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "total": total,
            "status_counts": status_counts,
            "time_filter": time_filter,
            "quick_ranges": DASHBOARD_QUICK_RANGES,
            "incident_sections": incident_sections,
            "store_timelines": build_store_timelines(
                db,
                timeline_stores,
                range_start=time_filter["start_utc"],
                range_end=time_filter["end_utc"],
            ),
            "timeline_range_label": time_filter["label"],
            "timeline_ticks": time_filter["ticks"],
            "show_timeline": True,
            "current_user": current_user,
            **monitor_context(db),
        },
    )


def _store_rows(db: Session, q: str = "", status: str = "") -> list[Store]:
    query = db.query(Store).outerjoin(StoreStatus)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Store.store_code.like(like) | Store.pc_name.like(like) | Store.ip_tunnel.like(like) | Store.area.like(like)
        )
    rows = query.order_by(Store.store_code).all()
    if status:
        rows = [store for store in rows if display_overall_status(store) == status]
    return rows


def _store_report_rows(stores: list[Store]) -> list[dict]:
    return [{header: getattr(store, field) for header, field in STORE_EXCEL_COLUMNS} for store in stores]


def _daily_timeline_window(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    timezone = _timezone(settings.timezone)
    local_now = now or datetime.now(timezone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=timezone)
    local_now = local_now.astimezone(timezone)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_now, day_start, day_start + timedelta(days=1)


def _timeline_label(start: datetime, end: datetime, status: str) -> str:
    duration_minutes = max(1, int((end - start).total_seconds() // 60))
    hours, minutes = divmod(duration_minutes, 60)
    duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    return " | ".join(
        [
            f"Status: {status}",
            f"Down: {start.strftime('%Y-%m-%d %H:%M')}",
            f"Up/End: {end.strftime('%Y-%m-%d %H:%M')}",
            f"Duration: {duration}",
        ]
    )


def build_store_timelines(
    db: Session,
    stores: list[Store],
    now: datetime | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> dict[int, list[dict]]:
    store_ids = [store.id for store in stores]
    if not store_ids:
        return {}

    timezone = _timezone(settings.timezone)
    local_now, day_start, day_end = _daily_timeline_window(now)
    timeline_start = range_start.replace(tzinfo=UTC).astimezone(timezone) if range_start else day_start
    timeline_end = range_end.replace(tzinfo=UTC).astimezone(timezone) if range_end else day_end
    if timeline_end <= timeline_start:
        return {store_id: [] for store_id in store_ids}

    timeline_start_utc = timeline_start.astimezone(UTC).replace(tzinfo=None)
    timeline_end_utc = timeline_end.astimezone(UTC).replace(tzinfo=None)
    local_now_utc = local_now.astimezone(UTC).replace(tzinfo=None)
    incidents = (
        db.query(Incident)
        .filter(
            Incident.store_id.in_(store_ids),
            Incident.started_at < timeline_end_utc,
            or_(Incident.ended_at.is_(None), Incident.ended_at > timeline_start_utc),
        )
        .order_by(Incident.started_at.asc())
        .all()
    )
    total_seconds = (timeline_end - timeline_start).total_seconds()
    timelines = {store_id: [] for store_id in store_ids}
    for incident in incidents:
        started_at = incident.started_at.replace(tzinfo=UTC).astimezone(timezone)
        raw_end = incident.ended_at or local_now_utc
        ended_at = raw_end.replace(tzinfo=UTC).astimezone(timezone)
        segment_start = max(started_at, timeline_start)
        segment_end = min(ended_at, timeline_end)
        if segment_end <= segment_start:
            continue
        left = ((segment_start - timeline_start).total_seconds() / total_seconds) * 100
        width = ((segment_end - segment_start).total_seconds() / total_seconds) * 100
        timelines[incident.store_id].append(
            {
                "left": round(left, 3),
                "width": round(width, 3),
                "status": incident.incident_type,
                "label": _timeline_label(segment_start, segment_end, incident.incident_type),
            }
        )
    return timelines


@app.get("/stores", response_class=HTMLResponse)
def stores(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    rows = _store_rows(db, q, status)
    visible_stores = rows[:1000]
    filtered_count = len(rows)
    return templates.TemplateResponse(
        request,
        "stores.html",
        {
            "stores": visible_stores,
            "store_timelines": build_store_timelines(db, visible_stores),
            "timeline_range_label": "00:00 → 24:00",
            "timeline_ticks": ["00", "06", "12", "18", "24"],
            "filtered_count": filtered_count,
            "q": q,
            "status": status,
            "current_user": current_user,
            **monitor_context(db),
        },
    )


@app.get("/stores/export")
def stores_export(q: str = "", status: str = "", db: Session = Depends(get_db), _current_user: str = Depends(require_auth)):
    content = build_store_report(_store_report_rows(_store_rows(db, q, status)))
    filename = f"store_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/stores/new", response_class=HTMLResponse)
def store_new(request: Request, current_user: str = Depends(require_auth)):
    data = _store_form_data()
    return templates.TemplateResponse(request, "store_form.html", _store_form_context(current_user, data, []))


@app.post("/stores")
def store_create(
    request: Request,
    store_code: str = Form(""),
    pc_name: str = Form(""),
    ip_local: str = Form(""),
    ip_tunnel: str = Form(""),
    wan_dns: str = Form(""),
    region: str = Form(""),
    area: str = Form(""),
    address: str = Form(""),
    enabled: str = Form("0"),
    db: Session = Depends(get_db),
    current_user: str = Depends(require_auth),
):
    data = _store_form_data(
        {
            "store_code": store_code,
            "pc_name": pc_name,
            "ip_local": ip_local,
            "ip_tunnel": ip_tunnel,
            "wan_dns": wan_dns,
            "region": region,
            "area": area,
            "address": address,
        },
        enabled == "1",
    )
    errors = _validate_store_form(db, data)
    if errors:
        return templates.TemplateResponse(
            request,
            "store_form.html",
            _store_form_context(current_user, data, errors),
            status_code=400,
        )

    store = Store(store_code=data["store_code"], enabled=data["enabled"])
    set_store_optional_fields(store, data)
    db.add(store)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        errors = ["Mã CH đã tồn tại."]
        return templates.TemplateResponse(
            request,
            "store_form.html",
            _store_form_context(current_user, data, errors),
            status_code=400,
        )
    ensure_store_status(db, store)
    db.commit()
    return RedirectResponse(url=f"/stores/{store.id}?created=1", status_code=303)


@app.get("/stores/{store_id}/edit", response_class=HTMLResponse)
def store_edit(request: Request, store_id: int, db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    data = _store_form_data(store)
    return templates.TemplateResponse(request, "store_form.html", _store_form_context(current_user, data, [], store))


@app.post("/stores/{store_id}/edit")
def store_update(
    request: Request,
    store_id: int,
    pc_name: str = Form(""),
    ip_local: str = Form(""),
    ip_tunnel: str = Form(""),
    wan_dns: str = Form(""),
    region: str = Form(""),
    area: str = Form(""),
    address: str = Form(""),
    enabled: str = Form("0"),
    db: Session = Depends(get_db),
    current_user: str = Depends(require_auth),
):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")

    data = _store_form_data(
        {
            "store_code": store.store_code,
            "pc_name": pc_name,
            "ip_local": ip_local,
            "ip_tunnel": ip_tunnel,
            "wan_dns": wan_dns,
            "region": region,
            "area": area,
            "address": address,
        },
        enabled == "1",
    )
    errors = _validate_store_form(db, data, store_id=store.id)
    if errors:
        return templates.TemplateResponse(
            request,
            "store_form.html",
            _store_form_context(current_user, data, errors, store),
            status_code=400,
        )

    set_store_optional_fields(store, data)
    store.enabled = data["enabled"]
    db.commit()
    return RedirectResponse(url=f"/stores/{store.id}?updated=1", status_code=303)


@app.post("/stores/{store_id}/delete")
def store_delete(store_id: int, db: Session = Depends(get_db), _current_user: str = Depends(require_auth)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")

    db.query(Incident).filter(Incident.store_id == store.id).delete(synchronize_session=False)
    db.query(StoreStatus).filter(StoreStatus.store_id == store.id).delete(synchronize_session=False)
    db.delete(store)
    db.commit()
    return RedirectResponse(url="/stores?deleted=1", status_code=303)


@app.get("/stores/{store_id}", response_class=HTMLResponse)
def store_detail(request: Request, store_id: int, db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    incidents = (
        db.query(Incident)
        .filter(Incident.store_id == store.id)
        .order_by(Incident.started_at.desc())
        .limit(100)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "store_detail.html",
        {"store": store, "incidents": incidents, "current_user": current_user, **monitor_context(db)},
    )


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, current_user: str = Depends(require_auth)):
    return templates.TemplateResponse(
        request,
        "import.html",
        {"current_user": current_user, "store_excel_headers": STORE_EXCEL_HEADERS},
    )


def _safe_upload_name(filename: str | None) -> str:
    original = Path(filename or "import.xlsx").name
    safe = "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in original)
    if not safe or safe in {".", ".."}:
        safe = "import.xlsx"
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe}"


def _pending_import_path(token: str) -> Path:
    if not token or not all(char.isalnum() or char in {"-", "_"} for char in token):
        raise HTTPException(status_code=404, detail="Pending import not found")
    path = PREVIEW_DIR / f"{token}.xlsx"
    if not path.exists() or path.parent != PREVIEW_DIR:
        raise HTTPException(status_code=404, detail="Pending import not found")
    return path


def _import_summary_params(result: dict) -> str:
    params = (
        f"created={result['created']}&updated={result['updated']}&errors={len(result['errors'])}"
        f"&valid_rows={result['valid_rows']}"
        f"&skipped_blank_fields={result['skipped_blank_fields']}"
        f"&skipped_missing_column_fields={result['skipped_missing_column_fields']}"
    )
    if result.get("backup_path"):
        params += "&backup=1"
    return params


def _render_import_preview(request: Request, file: UploadFile, db: Session, current_user: str):
    token = secrets.token_urlsafe(24)
    target = PREVIEW_DIR / f"{token}.xlsx"
    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    preview = preview_excel(db, target)
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {"token": token, "preview": preview, "current_user": current_user, "store_excel_headers": STORE_EXCEL_HEADERS},
    )


@app.post("/import/preview", response_class=HTMLResponse)
def import_preview(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    return _render_import_preview(request, file, db, current_user)


@app.post("/import")
def import_upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    return _render_import_preview(request, file, db, current_user)


@app.post("/import/confirm")
def import_confirm(token: str = Form(""), db: Session = Depends(get_db), _current_user: str = Depends(require_auth)):
    path = _pending_import_path(token)
    result = import_excel(db, path)
    path.unlink(missing_ok=True)
    return RedirectResponse(url=f"/import?{_import_summary_params(result)}", status_code=303)


@app.post("/import/cancel")
def import_cancel(token: str = Form(""), _current_user: str = Depends(require_auth)):
    path = _pending_import_path(token)
    path.unlink(missing_ok=True)
    return RedirectResponse(url="/import?cancelled=1", status_code=303)


def _incident_query(db: Session, status: str = "", store_code: str = "", from_date: str = "", to_date: str = ""):
    query = db.query(Incident, Store).join(Store, Store.id == Incident.store_id)
    if status:
        query = query.filter(Incident.status == status)
    if store_code:
        query = query.filter(Store.store_code.like(f"%{store_code}%"))
    if from_date:
        query = query.filter(Incident.started_at >= from_date)
    if to_date:
        query = query.filter(Incident.started_at <= to_date)
    return query.order_by(Incident.started_at.desc())


@app.get("/incidents", response_class=HTMLResponse)
def incidents(
    request: Request,
    status: str = "",
    store_code: str = "",
    from_date: str = "",
    to_date: str = "",
    db: Session = Depends(get_db),
    current_user: str = Depends(require_auth),
):
    rows = _incident_query(db, status, store_code, from_date, to_date).limit(500).all()
    return templates.TemplateResponse(
        request,
        "incidents.html",
        {
            "rows": rows,
            "current_user": current_user,
            "status": status,
            "store_code": store_code,
            "from_date": from_date,
            "to_date": to_date,
        },
    )


@app.get("/incidents/export")
def incidents_export(
    status: str = "",
    store_code: str = "",
    from_date: str = "",
    to_date: str = "",
    db: Session = Depends(get_db),
    _current_user: str = Depends(require_auth),
):
    rows = _incident_query(db, status, store_code, from_date, to_date).limit(5000).all()
    content = build_incident_report(rows)
    filename = f"incident_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/monitor/run-once")
async def monitor_run_once(return_to: str = Form(""), _current_user: str = Depends(require_auth)):
    result = await run_once()
    if return_to:
        return RedirectResponse(url=_safe_redirect_path(return_to), status_code=303)
    return result


@app.post("/telegram/test")
async def telegram_test(_current_user: str = Depends(require_auth)):
    ok = await send_telegram("✅ TH Network Monitor test alert")
    return {"sent": ok}


@app.get("/backups", response_class=HTMLResponse)
def backups_page(request: Request, current_user: str = Depends(require_auth)):
    db_path = sqlite_db_path()
    backups = list_backups() if db_path else []
    return templates.TemplateResponse(
        request,
        "backups.html",
        {"current_user": current_user, "db_path": db_path, "backups": backups},
    )


@app.post("/backups/create")
def backups_create(_current_user: str = Depends(require_auth)):
    backup_path = create_sqlite_backup("manual")
    if backup_path is None:
        return RedirectResponse(url="/backups?unsupported=1", status_code=303)
    return RedirectResponse(url="/backups?created=1", status_code=303)


@app.get("/backups/download/{name}")
def backups_download(name: str, _current_user: str = Depends(require_auth)):
    try:
        path = resolve_backup(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@app.post("/backups/delete")
def backups_delete(name: str = Form(""), _current_user: str = Depends(require_auth)):
    try:
        path = resolve_backup(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    path.unlink()
    return RedirectResponse(url="/backups?deleted=1", status_code=303)


@app.post("/backups/restore")
def backups_restore(name: str = Form(""), _current_user: str = Depends(require_auth)):
    try:
        path = resolve_backup(name)
        pre_restore = restore_sqlite_backup(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except RuntimeError:
        return RedirectResponse(url="/backups?unsupported=1", status_code=303)
    return RedirectResponse(url=f"/backups?restored=1&pre_restore={pre_restore.name}", status_code=303)


@app.get("/api/stores")
def api_stores(_current_user: str = Depends(require_auth), db: Session = Depends(get_db)):
    return db.query(Store).outerjoin(StoreStatus).order_by(Store.store_code).limit(1000).all()
