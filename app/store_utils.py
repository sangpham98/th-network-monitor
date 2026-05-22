import ipaddress
from collections.abc import Mapping

from app.models import Store, StoreStatus

OPTIONAL_FIELDS = ["pc_name", "ip_local", "ip_tunnel", "wan_dns", "region", "area", "address"]
IMPORT_FIELDS = ["store_code", *OPTIONAL_FIELDS]
IP_FIELDS = ["ip_local", "ip_tunnel"]
STORE_FORM_FIELDS = ["store_code", *OPTIONAL_FIELDS]
STORE_CODE_LENGTHS = (7, 8)
STORE_CODE_PREFIX = "70000"


def clean_store_value(value: object) -> str | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        return None
    value = str(value).strip()
    return value or None


def valid_store_code_format(code: object) -> bool:
    if not code or not isinstance(code, str):
        return False
    if len(code) not in STORE_CODE_LENGTHS:
        return False
    if not code.isdigit():
        return False
    if not code.startswith(STORE_CODE_PREFIX):
        return False
    return True


def valid_ip(value: object) -> bool:
    if not value:
        return True
    try:
        ipaddress.ip_address(str(value))
        return True
    except ValueError:
        return False


def set_store_optional_fields(store: Store, data: Mapping[str, object]) -> None:
    for field in OPTIONAL_FIELDS:
        setattr(store, field, data.get(field))


def ensure_store_status(db, store: Store) -> None:
    if not db.query(StoreStatus).filter(StoreStatus.store_id == store.id).first():
        db.add(StoreStatus(store_id=store.id))
