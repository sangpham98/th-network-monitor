import sqlite3

from sqlalchemy import create_engine, inspect, text

from app import database


def test_add_column_if_missing_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "migration.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", test_engine)

    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE store_status (store_id INTEGER PRIMARY KEY)"))

    database._add_column_if_missing("store_status", "wan_success_count", "INTEGER DEFAULT 0")
    database._add_column_if_missing("store_status", "wan_success_count", "INTEGER DEFAULT 0")

    columns = {column["name"] for column in inspect(test_engine).get_columns("store_status")}
    assert "wan_success_count" in columns


def test_sqlite_pragmas_can_enable_wal_and_busy_timeout(tmp_path):
    db_path = tmp_path / "pragma.db"
    connection = sqlite3.connect(db_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
        connection.execute("PRAGMA busy_timeout=5000;")
        busy_timeout = connection.execute("PRAGMA busy_timeout;").fetchone()[0]
    finally:
        connection.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000
