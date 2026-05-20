import pytest

from monitor import checker


@pytest.mark.asyncio
async def test_ping_host_uses_packet_count_and_deadline(monkeypatch):
    captured = {}

    class FakeProcess:
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(checker.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await checker.ping_host("10.0.0.1", timeout=2, retry=5) is True
    assert captured["args"][:8] == ("ping", "-c", "5", "-i", "0.5", "-W", "2", "10.0.0.1")


@pytest.mark.asyncio
async def test_ping_host_returns_false_when_all_retry_fail(monkeypatch):
    class FakeProcess:
        async def wait(self):
            return 1

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(checker.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await checker.ping_host("10.0.0.1", timeout=1, retry=3) is False


@pytest.mark.asyncio
async def test_check_wan_pings_target_directly(monkeypatch):
    captured = {}

    async def fake_ping_host(host, timeout, retry):
        captured["call"] = (host, timeout, retry)
        return True

    monkeypatch.setattr(checker, "ping_host", fake_ping_host)

    assert await checker.check_wan("wan.example", timeout=2, retry=8) is True
    assert captured["call"] == ("wan.example", 2, 8)
