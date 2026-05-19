from datetime import UTC, datetime

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

IS_SQLITE = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_column_if_missing(table_name: str, column_name: str, column_definition: str):
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing_columns:
        return

    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"))


def _backfill_alert_sent_at():
    inspector = inspect(engine)
    if "incidents" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("incidents")}
    if "alert_sent_at" not in columns:
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE incidents
                SET alert_sent_at = :now
                WHERE status = 'OPEN'
                  AND alert_sent = 1
                  AND alert_sent_at IS NULL
                """
            ),
            {"now": now},
        )


def run_sqlite_migrations():
    if not IS_SQLITE:
        return

    _add_column_if_missing("store_status", "wan_success_count", "INTEGER DEFAULT 0")
    _add_column_if_missing("store_status", "tunnel_success_count", "INTEGER DEFAULT 0")
    _add_column_if_missing("store_status", "wan_down_window", "TEXT DEFAULT ''")
    _add_column_if_missing("store_status", "tunnel_down_window", "TEXT DEFAULT ''")
    _add_column_if_missing("incidents", "alert_sent_at", "DATETIME")
    _add_column_if_missing("incidents", "last_reminder_at", "DATETIME")
    _add_column_if_missing("incidents", "reminder_count", "INTEGER DEFAULT 0")
    _backfill_alert_sent_at()


def init_db():
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_sqlite_migrations()
