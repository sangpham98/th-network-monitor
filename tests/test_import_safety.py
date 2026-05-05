from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import backups
from app.database import Base
from app.models import Store
from importers import excel_importer
from importers.excel_importer import import_excel, parse_excel


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def write_excel(path: Path, rows: list[dict]):
    pd.DataFrame(rows).to_excel(path, index=False)


def test_missing_optional_column_does_not_clear_existing_value(tmp_path):
    db = make_db()
    store = Store(store_code="70000123", pc_name="PC001", wan_dns="old.example", ip_tunnel="10.0.0.1")
    db.add(store)
    db.commit()

    path = tmp_path / "missing-column.xlsx"
    write_excel(path, [{"Mã CH": "70000123", "PC Name": "PC002"}])

    result = import_excel(db, path)
    db.refresh(store)

    assert result["updated"] == 1
    assert store.pc_name == "PC002"
    assert store.wan_dns == "old.example"
    assert store.ip_tunnel == "10.0.0.1"
    assert result["skipped_missing_column_fields"] > 0


def test_blank_cell_does_not_clear_existing_value(tmp_path):
    db = make_db()
    store = Store(store_code="70000123", pc_name="PC001", wan_dns="old.example", ip_tunnel="10.0.0.1")
    db.add(store)
    db.commit()

    path = tmp_path / "blank-cell.xlsx"
    write_excel(path, [{"Mã CH": "70000123", "WAN DNS": "", "IP Tunnel": "10.0.0.2"}])

    result = import_excel(db, path)
    db.refresh(store)

    assert result["updated"] == 1
    assert store.wan_dns == "old.example"
    assert store.ip_tunnel == "10.0.0.2"
    assert result["skipped_blank_fields"] > 0


def test_invalid_ip_returns_row_error(tmp_path):
    path = tmp_path / "invalid-ip.xlsx"
    write_excel(path, [{"Mã CH": "70000123", "IP Tunnel": "not-an-ip"}])

    rows, errors, _present_fields = parse_excel(path)

    assert rows == []
    assert len(errors) == 1
    assert "không hợp lệ" in errors[0]["error"]


def test_duplicate_store_code_updates_existing(tmp_path):
    db = make_db()
    store = Store(store_code="70000123", pc_name="PC001")
    db.add(store)
    db.commit()

    path = tmp_path / "update.xlsx"
    write_excel(path, [{"Mã CH": "70000123", "PC Name": "PC002"}])

    result = import_excel(db, path)
    db.refresh(store)

    assert result["created"] == 0
    assert result["updated"] == 1
    assert store.pc_name == "PC002"


def test_large_import_creates_sqlite_backup(tmp_path, monkeypatch):
    db_file = tmp_path / "network_monitor.db"
    db_file.write_bytes(b"sqlite-db")
    monkeypatch.setattr(backups.settings, "database_url", f"sqlite:///{db_file}")
    monkeypatch.setattr(backups.settings, "data_dir", tmp_path)

    backup_path = excel_importer.backup_sqlite_db_if_needed(51)

    assert backup_path is not None
    assert Path(backup_path).exists()
    assert Path(backup_path).read_bytes() == b"sqlite-db"


def test_small_import_does_not_create_backup(tmp_path, monkeypatch):
    db_file = tmp_path / "network_monitor.db"
    db_file.write_bytes(b"sqlite-db")
    monkeypatch.setattr(backups.settings, "database_url", f"sqlite:///{db_file}")
    monkeypatch.setattr(backups.settings, "data_dir", tmp_path)

    backup_path = excel_importer.backup_sqlite_db_if_needed(50)

    assert backup_path is None
