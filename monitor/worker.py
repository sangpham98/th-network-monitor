import asyncio

from alerts.telegram import send_telegram
from app.config import settings
from app.database import SessionLocal, init_db
from app.models import Store
from monitor.checker import check_wan, ping_host
from monitor.status_engine import format_alert, update_status_and_incident


async def check_store(store: Store, semaphore: asyncio.Semaphore):
    async with semaphore:
        wan_ok = await check_wan(store.wan_dns, settings.ping_timeout_seconds) if store.wan_dns else None
        tunnel_ok = await ping_host(store.ip_tunnel, settings.ping_timeout_seconds) if store.ip_tunnel else None
        return store.id, wan_ok, tunnel_ok


async def run_once():
    db = SessionLocal()
    try:
        stores = db.query(Store).filter(Store.enabled.is_(True)).all()
        semaphore = asyncio.Semaphore(settings.max_concurrency)
        results = await asyncio.gather(*(check_store(store, semaphore) for store in stores))

        store_by_id = {store.id: store for store in stores}
        alerts = []
        for store_id, wan_ok, tunnel_ok in results:
            store = store_by_id[store_id]
            changed, status, _old, recovered = update_status_and_incident(
                db=db,
                store=store,
                wan_ok=wan_ok,
                tunnel_ok=tunnel_ok,
                down_threshold=settings.down_threshold,
            )
            if changed:
                alerts.append(format_alert(store, status, recovered=recovered))

        db.commit()
        for message in alerts:
            await send_telegram(message)

        return {"checked": len(stores), "alerts": len(alerts)}
    finally:
        db.close()


async def run_forever():
    init_db()
    while True:
        try:
            result = await run_once()
            print(f"monitor checked={result['checked']} alerts={result['alerts']}")
        except Exception as exc:  # keep worker alive
            print(f"monitor error: {exc}")
        await asyncio.sleep(settings.monitor_interval_seconds)


if __name__ == "__main__":
    asyncio.run(run_forever())
