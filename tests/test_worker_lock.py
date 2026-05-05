import pytest
from filelock import FileLock

from monitor import worker


@pytest.mark.asyncio
async def test_run_once_skips_when_lock_busy(tmp_path, monkeypatch):
    lock_path = tmp_path / "monitor.lock"
    monkeypatch.setattr(worker, "LOCK_PATH", lock_path)

    lock = FileLock(str(lock_path), timeout=0)
    with lock:
        result = await worker.run_once()

    assert result == {"status": "skipped", "reason": "monitor already running"}


@pytest.mark.asyncio
async def test_run_once_runs_when_lock_free(tmp_path, monkeypatch):
    lock_path = tmp_path / "monitor.lock"
    monkeypatch.setattr(worker, "LOCK_PATH", lock_path)

    async def fake_run_once_locked():
        return {"status": "ok", "checked": 0, "alerts": 0, "sent": 0}

    monkeypatch.setattr(worker, "_run_once_locked", fake_run_once_locked)

    result = await worker.run_once()

    assert result == {"status": "ok", "checked": 0, "alerts": 0, "sent": 0}
