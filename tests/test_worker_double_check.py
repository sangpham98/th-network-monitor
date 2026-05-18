import pytest

from monitor import worker


def make_event(status="DOWN", recovered=False):
    return {
        "store_id": 1,
        "store_code": "70000123",
        "pc_name": "PC001",
        "region": "North",
        "area": "Area 1",
        "address": "123 Main",
        "status": status,
        "wan_dns": "wan.example",
        "ip_tunnel": "10.0.0.1",
        "incident_ids": [42],
        "recovered": recovered,
    }


@pytest.mark.asyncio
async def test_double_check_suppresses_down_alert_when_store_is_up(monkeypatch):
    async def check_wan(_target, _timeout, _retry):
        return True

    async def ping_host(_target, _timeout, _retry):
        return True

    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)

    events = await worker._double_check_down_alert_events([make_event()])

    assert events == []


@pytest.mark.asyncio
async def test_double_check_keeps_down_alert_when_store_still_fails(monkeypatch):
    async def check_wan(_target, _timeout, _retry):
        return False

    async def ping_host(_target, _timeout, _retry):
        return True

    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)

    events = await worker._double_check_down_alert_events([make_event()])

    assert len(events) == 1
    assert events[0]["status"] == "WAN_DOWN"
    assert events[0]["incident_ids"] == [42]


@pytest.mark.asyncio
async def test_double_check_passes_recovery_events_without_rechecking(monkeypatch):
    async def check_wan(_target, _timeout, _retry):
        raise AssertionError("recovery event should not be rechecked")

    async def ping_host(_target, _timeout, _retry):
        raise AssertionError("recovery event should not be rechecked")

    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    event = make_event(status="UP", recovered=True)

    events = await worker._double_check_down_alert_events([event])

    assert events == [event]
