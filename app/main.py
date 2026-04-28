import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from alerts.telegram import send_telegram
from app.database import get_db, init_db
from app.models import Incident, Store, StoreStatus
from importers.excel_importer import import_excel
from monitor.worker import run_once

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="TH Network Monitor")
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    total = db.query(Store).count()
    status_counts = dict(db.query(StoreStatus.overall_status, func.count()).group_by(StoreStatus.overall_status).all())
    stores = db.query(Store).outerjoin(StoreStatus).order_by(Store.store_code).limit(100).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "total": total, "status_counts": status_counts, "stores": stores},
    )


@app.get("/stores", response_class=HTMLResponse)
def stores(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db)):
    query = db.query(Store).outerjoin(StoreStatus)
    if q:
        like = f"%{q}%"
        query = query.filter(
            Store.store_code.like(like) | Store.pc_name.like(like) | Store.ip_tunnel.like(like) | Store.area.like(like)
        )
    if status:
        query = query.filter(StoreStatus.overall_status == status)
    rows = query.order_by(Store.store_code).limit(1000).all()
    return templates.TemplateResponse("stores.html", {"request": request, "stores": rows, "q": q, "status": status})


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})


@app.post("/import")
def import_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    target = UPLOAD_DIR / file.filename
    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    result = import_excel(db, target)
    return RedirectResponse(url=f"/import?created={result['created']}&updated={result['updated']}&errors={len(result['errors'])}", status_code=303)


@app.get("/incidents", response_class=HTMLResponse)
def incidents(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Incident, Store).join(Store, Store.id == Incident.store_id).order_by(Incident.started_at.desc()).limit(500).all()
    return templates.TemplateResponse("incidents.html", {"request": request, "rows": rows})


@app.post("/monitor/run-once")
async def monitor_run_once():
    return await run_once()


@app.post("/telegram/test")
async def telegram_test():
    ok = await send_telegram("✅ TH Network Monitor test alert")
    return {"sent": ok}


@app.get("/api/stores")
def api_stores(db: Session = Depends(get_db)):
    return db.query(Store).outerjoin(StoreStatus).order_by(Store.store_code).limit(1000).all()
