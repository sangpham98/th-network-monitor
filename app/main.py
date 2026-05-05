import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from alerts.telegram import send_telegram
from app.backups import create_sqlite_backup, list_backups, resolve_backup, restore_sqlite_backup, sqlite_db_path
from app.auth import auth_configured, clear_login_cookie, require_auth, set_login_cookie
from app.database import get_db, init_db
from app.logging_config import configure_logging
from app.models import Incident, Store, StoreStatus
from app.reports import build_incident_report
from importers.excel_importer import import_excel, preview_excel
from monitor.worker import run_once

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
PREVIEW_DIR = BASE_DIR / "data" / "import_previews"


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
def dashboard(request: Request, db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    total = db.query(Store).count()
    status_counts = dict(db.query(StoreStatus.overall_status, func.count()).group_by(StoreStatus.overall_status).all())
    stores = db.query(Store).outerjoin(StoreStatus).order_by(Store.store_code).limit(100).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"total": total, "status_counts": status_counts, "stores": stores, "current_user": current_user},
    )


@app.get("/stores", response_class=HTMLResponse)
def stores(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db), current_user: str = Depends(require_auth)):
    query = db.query(Store).outerjoin(StoreStatus)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Store.store_code.like(like) | Store.pc_name.like(like) | Store.ip_tunnel.like(like) | Store.area.like(like)
        )
    if status:
        query = query.filter(StoreStatus.overall_status == status)
    rows = query.order_by(Store.store_code).limit(1000).all()
    return templates.TemplateResponse(request, "stores.html", {"stores": rows, "q": q, "status": status, "current_user": current_user})


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
        {"store": store, "incidents": incidents, "current_user": current_user},
    )


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, current_user: str = Depends(require_auth)):
    return templates.TemplateResponse(request, "import.html", {"current_user": current_user})


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
        {"token": token, "preview": preview, "current_user": current_user},
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
async def monitor_run_once(_current_user: str = Depends(require_auth)):
    return await run_once()


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
