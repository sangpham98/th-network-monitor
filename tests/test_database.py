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
    database._add_column_if_missing("store_status", "wan_down_window", "TEXT DEFAULT ''")
    database._add_column_if_missing("store_status", "tunnel_down_window", "TEXT DEFAULT ''")

    columns = {column["name"] for column in inspect(test_engine).get_columns("store_status")}
    assert "wan_success_count" in columns
    assert "wan_down_window" in columns
    assert "tunnel_down_window" in columns


def test_sqlite_migrations_add_reminder_columns_and_backfill_open_alerts(tmp_path, monkeypatch):
    db_path = tmp_path / "reminder_migration.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "IS_SQLITE", True)

    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE store_status (store_id INTEGER PRIMARY KEY)"))
        connection.execute(
            text(
                """
                CREATE TABLE incidents (
                    id INTEGER PRIMARY KEY,
                    store_id INTEGER,
                    incident_type TEXT,
                    status TEXT,
                    started_at DATETIME,
                    alert_sent BOOLEAN,
                    recovery_sent BOOLEAN
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO incidents (id, store_id, incident_type, status, started_at, alert_sent, recovery_sent)
                VALUES
                    (1, 1, 'DOWN', 'OPEN', '2026-01-01 00:00:00', 1, 0),
                    (2, 1, 'DOWN', 'RESOLVED', '2026-01-01 00:00:00', 1, 1),
                    (3, 1, 'DOWN', 'OPEN', '2026-01-01 00:00:00', 0, 0)
                """
            )
        )

    database.run_sqlite_migrations()
    database.run_sqlite_migrations()

    incident_columns = {column["name"]: column for column in inspect(test_engine).get_columns("incidents")}
    assert "alert_sent_at" in incident_columns
    assert "last_reminder_at" in incident_columns
    assert "reminder_count" in incident_columns

    store_status_columns = {column["name"] for column in inspect(test_engine).get_columns("store_status")}
    assert "tunnel_success_count" in store_status_columns

    with test_engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, alert_sent_at, last_reminder_at, reminder_count FROM incidents ORDER BY id")
        ).mappings().all()

    assert rows[0]["alert_sent_at"] is not None
    assert rows[0]["last_reminder_at"] is None
    assert rows[0]["reminder_count"] == 0
    assert rows[1]["alert_sent_at"] is None
    assert rows[2]["alert_sent_at"] is None


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
