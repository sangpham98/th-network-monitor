import ipaddress
from pathlib import Path

import pandas as pd

from app.models import Store, StoreStatus

COLUMN_MAP = {
    "Mã CH": "store_code",
    "Ma CH": "store_code",
    "Store Code": "store_code",
    "PC Name": "pc_name",
    "IP Local": "ip_local",
    "IP tunel": "ip_tunnel",
    "IP Tunnel": "ip_tunnel",
    "WAN DNS": "wan_dns",
    "DNS": "wan_dns",
    "Domain": "wan_dns",
    "Miền": "region",
    "Mien": "region",
    "Khu vực": "area",
    "Khu vuc": "area",
    "Địa chỉ": "address",
    "Dia chi": "address",
}


def clean(value):
    if pd.isna(value):
        return None
    return str(value).strip()


def valid_ip(value):
    if not value:
        return True
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def parse_excel(path: Path):
    df = pd.read_excel(path)
    df = df.rename(columns={c: COLUMN_MAP.get(str(c).strip(), str(c).strip()) for c in df.columns})
    rows, errors = [], []

    for idx, raw in df.iterrows():
        row_no = idx + 2
        item = {
            key: clean(raw.get(key))
            for key in ["store_code", "pc_name", "ip_local", "ip_tunnel", "wan_dns", "region", "area", "address"]
        }

        if not item["store_code"]:
            errors.append({"row": row_no, "error": "Thiếu Mã CH"})
            continue
        if not valid_ip(item["ip_local"]):
            errors.append({"row": row_no, "error": "IP Local không hợp lệ"})
            continue
        if not valid_ip(item["ip_tunnel"]):
            errors.append({"row": row_no, "error": "IP Tunnel không hợp lệ"})
            continue

        rows.append(item)

    return rows, errors


def import_excel(db, path: Path):
    rows, errors = parse_excel(path)
    created = updated = 0

    for item in rows:
        store = db.query(Store).filter(Store.store_code == item["store_code"]).first()
        if store:
            updated += 1
        else:
            store = Store(store_code=item["store_code"])
            db.add(store)
            created += 1

        for key, value in item.items():
            setattr(store, key, value)

        db.flush()
        if not store.status:
            db.add(StoreStatus(store_id=store.id))

    db.commit()
    return {"created": created, "updated": updated, "errors": errors, "valid_rows": len(rows)}
