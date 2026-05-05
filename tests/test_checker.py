import pytest

from monitor import checker


@pytest.mark.asyncio
async def test_ping_host_uses_retry(monkeypatch):
    captured = {}

    class FakeProcess:
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(checker.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await checker.ping_host("10.0.0.1", timeout=2, retry=3) is True
    assert captured["args"][:6] == ("ping", "-c", "3", "-W", "2", "10.0.0.1")


@pytest.mark.asyncio
async def test_ping_host_returns_false_when_all_retry_fail(monkeypatch):
    class FakeProcess:
        async def wait(self):
            return 1

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(checker.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await checker.ping_host("10.0.0.1", timeout=1, retry=3) is False
