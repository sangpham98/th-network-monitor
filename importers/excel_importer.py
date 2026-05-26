from pathlib import Path

import pandas as pd

from app.backups import create_sqlite_backup
from app.models import Store
from app.store_utils import (
    IMPORT_FIELDS,
    IP_FIELDS,
    OPTIONAL_FIELDS,
    STORE_EXCEL_FIELD_BY_HEADER,
    clean_store_value,
    ensure_store_status,
    valid_ip,
    valid_store_code_format,
)

COLUMN_MAP = {
    **STORE_EXCEL_FIELD_BY_HEADER,
    "Ma CH": "store_code",
    "Store Code": "store_code",
    "IP tunel": "ip_tunnel",
    "WAN\x7fDNS": "wan_dns",
    "WAN_DNS": "wan_dns",
    "DNS WAN": "wan_dns",
    "DNS_WAN": "wan_dns",
    "DNS": "wan_dns",
    "Domain": "wan_dns",
    "Mien": "region",
    "Khu vuc": "area",
    "Dia chi": "address",
}
BACKUP_THRESHOLD_ROWS = 50


def parse_excel(path: Path):
    df = pd.read_excel(path)
    df = df.rename(columns={c: COLUMN_MAP.get(str(c).strip(), str(c).strip()) for c in df.columns})
    present_fields = {field for field in IMPORT_FIELDS if field in df.columns}
    rows, errors = [], []
    seen_codes = {}

    for idx, raw in df.iterrows():
        row_no = idx + 2
        item = {key: clean_store_value(raw.get(key)) for key in present_fields}

        if not item.get("store_code"):
            errors.append({"row": row_no, "error": "Thiếu Mã CH"})
            continue

        store_code = item.get("store_code")
        if not valid_store_code_format(store_code):
            errors.append({"row": row_no, "error": f"Mã CH '{store_code}' không đúng định dạng (cần 7 số, bắt đầu bằng 70000)"})
            continue

        if store_code in seen_codes:
            errors.append({"row": row_no, "error": f"Mã CH '{store_code}' bị trùng (xuất hiện lần đầu ở dòng {seen_codes[store_code]})"})
            continue
        seen_codes[store_code] = row_no

        invalid_ip_field = None
        for key in IP_FIELDS:
            if key in item and not valid_ip(item.get(key)):
                invalid_ip_field = key
                break
        if invalid_ip_field:
            errors.append({"row": row_no, "error": f"{invalid_ip_field} không hợp lệ"})
            continue

        rows.append({"row": row_no, "data": item})

    return rows, errors, present_fields


def backup_sqlite_db_if_needed(valid_rows: int) -> str | None:
    if valid_rows <= BACKUP_THRESHOLD_ROWS:
        return None

    backup_path = create_sqlite_backup("import")
    return str(backup_path) if backup_path else None


def _apply_store_fields(store: Store, item: dict, present_fields: set[str], is_new: bool) -> tuple[int, int]:
    skipped_blank_fields = 0
    skipped_missing_column_fields = 0

    for key in OPTIONAL_FIELDS:
        if key not in present_fields:
            skipped_missing_column_fields += 1
            continue

        value = item.get(key)
        if value is None:
            skipped_blank_fields += 1
            if is_new:
                setattr(store, key, None)
            continue

        setattr(store, key, value)

    return skipped_blank_fields, skipped_missing_column_fields


def _count_skipped_fields(item: dict, present_fields: set[str]) -> tuple[int, int]:
    skipped_blank_fields = 0
    skipped_missing_column_fields = 0

    for key in OPTIONAL_FIELDS:
        if key not in present_fields:
            skipped_missing_column_fields += 1
            continue
        if item.get(key) is None:
            skipped_blank_fields += 1

    return skipped_blank_fields, skipped_missing_column_fields


def preview_excel(db, path: Path, sample_size: int = 20):
    rows, errors, present_fields = parse_excel(path)
    would_create = would_update = 0
    skipped_blank_fields = 0
    skipped_missing_column_fields = 0
    samples = []

    for parsed in rows:
        item = parsed["data"]
        exists = db.query(Store.id).filter(Store.store_code == item["store_code"]).first() is not None
        if exists:
            would_update += 1
        else:
            would_create += 1

        blank_count, missing_count = _count_skipped_fields(item, present_fields)
        skipped_blank_fields += blank_count
        skipped_missing_column_fields += missing_count

        if len(samples) < sample_size:
            samples.append({"row": parsed["row"], **item})

    return {
        "valid_rows": len(rows),
        "errors": errors,
        "would_create": would_create,
        "would_update": would_update,
        "skipped_blank_fields": skipped_blank_fields,
        "skipped_missing_column_fields": skipped_missing_column_fields,
        "backup_required": len(rows) > BACKUP_THRESHOLD_ROWS,
        "samples": samples,
    }


def import_excel(db, path: Path):
    rows, errors, present_fields = parse_excel(path)
    created = updated = 0
    skipped_blank_fields = 0
    skipped_missing_column_fields = 0
    backup_path = backup_sqlite_db_if_needed(len(rows))

    for parsed in rows:
        item = parsed["data"]
        store = db.query(Store).filter(Store.store_code == item["store_code"]).first()
        is_new = store is None
        if is_new:
            store = Store(store_code=item["store_code"])
            db.add(store)
            created += 1
        else:
            updated += 1

        blank_count, missing_count = _apply_store_fields(store, item, present_fields, is_new)
        skipped_blank_fields += blank_count
        skipped_missing_column_fields += missing_count

        db.flush()
        ensure_store_status(db, store)

    db.commit()
    return {
        "created": created,
        "updated": updated,
        "errors": errors,
        "valid_rows": len(rows),
        "skipped_blank_fields": skipped_blank_fields,
        "skipped_missing_column_fields": skipped_missing_column_fields,
        "backup_path": backup_path,
    }
