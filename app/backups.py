import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR, settings


@dataclass
class BackupInfo:
    name: str
    size_bytes: int
    modified_at: datetime


def sqlite_db_path() -> Path | None:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        return None

    raw_path = settings.database_url[len(prefix) :]
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return db_path


def backup_dir() -> Path:
    path = settings.data_dir / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_sqlite_backup(label: str = "manual") -> Path | None:
    db_path = sqlite_db_path()
    if not db_path or not db_path.exists():
        return None

    safe_label = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label) or "manual"
    backup_path = backup_dir() / f"network_monitor_{safe_label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def list_backups() -> list[BackupInfo]:
    rows = []
    for path in backup_dir().glob("*.db"):
        stat = path.stat()
        rows.append(BackupInfo(name=path.name, size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime)))
    return sorted(rows, key=lambda item: item.modified_at, reverse=True)


def resolve_backup(name: str) -> Path:
    if not name or Path(name).name != name:
        raise FileNotFoundError(name)
    if not name.endswith(".db"):
        raise FileNotFoundError(name)
    path = backup_dir() / name
    if not path.exists() or path.parent != backup_dir():
        raise FileNotFoundError(name)
    return path


def restore_sqlite_backup(source: Path) -> Path:
    db_path = sqlite_db_path()
    if not db_path:
        raise RuntimeError("SQLite database is not configured")
    if not db_path.exists():
        raise RuntimeError("SQLite database file does not exist")

    pre_restore = create_sqlite_backup("pre_restore")
    if pre_restore is None:
        raise RuntimeError("Could not create pre-restore backup")

    shutil.copy2(source, db_path)
    return pre_restore
