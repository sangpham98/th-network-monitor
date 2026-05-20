import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Incident, Store, StoreStatus
from monitor import worker


class RecordingSession:
    def __init__(self, session, events):
        self.session = session
        self.events = events

    def commit(self):
        self.events.append("commit")
        return self.session.commit()

    def rollback(self):
        return self.session.rollback()

    def close(self):
        return self.session.close()

    def __getattr__(self, name):
        return getattr(self.session, name)


def make_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    events = []

    def session_local():
        return RecordingSession(session_factory(), events)

    return session_factory, session_local, events


def add_stores(session, count):
    session.add_all(
        [
            Store(
                store_code=f"CH{index:03d}",
                pc_name=f"PC{index:03d}",
                wan_dns=f"wan{index}.example",
                ip_tunnel=f"10.0.0.{index}",
            )
            for index in range(1, count + 1)
        ]
    )
    session.commit()


@pytest.mark.asyncio
async def test_run_once_pings_store_targets_then_commits_batch(tmp_path, monkeypatch):
    session_factory, session_local, events = make_session_factory()
    setup_db = session_factory()
    add_stores(setup_db, 3)
    setup_db.close()
    events.clear()
    calls = []

    async def check_wan(target, timeout, retry):
        calls.append(("wan", target, timeout, retry))
        return True

    async def ping_host(target, timeout, retry):
        calls.append(("tunnel", target, timeout, retry))
        return True

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "STATUS_PATH", tmp_path / "monitor_status.json")
    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    monkeypatch.setattr(worker.settings, "telegram_bot_token", "")
    monkeypatch.setattr(worker.settings, "telegram_chat_id", "")

    result = await worker._run_once_locked()

    assert result["checked"] == 3
    assert events == ["commit"]
    assert calls == [
        ("wan", "wan1.example", worker.settings.ping_timeout_seconds, 5),
        ("tunnel", "10.0.0.1", worker.settings.ping_timeout_seconds, 5),
        ("wan", "wan2.example", worker.settings.ping_timeout_seconds, 5),
        ("tunnel", "10.0.0.2", worker.settings.ping_timeout_seconds, 5),
        ("wan", "wan3.example", worker.settings.ping_timeout_seconds, 5),
        ("tunnel", "10.0.0.3", worker.settings.ping_timeout_seconds, 5),
    ]

    db = session_factory()
    assert db.query(StoreStatus).count() == 3
    assert {status.overall_status for status in db.query(StoreStatus).all()} == {"UP"}
    db.close()


@pytest.mark.asyncio
async def test_run_once_skips_placeholder_targets(tmp_path, monkeypatch):
    session_factory, session_local, events = make_session_factory()
    setup_db = session_factory()
    setup_db.add(Store(store_code="CH001", pc_name="PC001", wan_dns="0", ip_tunnel="-"))
    setup_db.commit()
    setup_db.close()
    events.clear()

    async def check_wan(_target, _timeout, _retry):
        raise AssertionError("placeholder WAN target should not be pinged")

    async def ping_host(_target, _timeout, _retry):
        raise AssertionError("placeholder tunnel target should not be pinged")

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "STATUS_PATH", tmp_path / "monitor_status.json")
    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    monkeypatch.setattr(worker.settings, "telegram_bot_token", "")
    monkeypatch.setattr(worker.settings, "telegram_chat_id", "")

    result = await worker._run_once_locked()

    assert result["checked"] == 1
    assert events == ["commit"]
    db = session_factory()
    status = db.query(StoreStatus).one()
    assert status.wan_status == "UNKNOWN"
    assert status.tunnel_status == "UNKNOWN"
    assert status.overall_status == "UNKNOWN"
    db.close()


@pytest.mark.asyncio
async def test_run_once_commits_each_50_store_batch_before_telegram(tmp_path, monkeypatch):
    session_factory, session_local, events = make_session_factory()
    setup_db = session_factory()
    add_stores(setup_db, 51)
    setup_db.close()
    events.clear()
    sent_messages = []
    ping_calls = []

    async def check_wan(target, _timeout, retry):
        events.append("ping")
        ping_calls.append(("wan", target, retry))
        return False

    async def ping_host(target, _timeout, retry):
        events.append("ping")
        ping_calls.append(("tunnel", target, retry))
        return True

    async def send_telegram(message):
        events.append("send")
        sent_messages.append(message)
        return False

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "STATUS_PATH", tmp_path / "monitor_status.json")
    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    monkeypatch.setattr(worker, "send_telegram", send_telegram)
    monkeypatch.setattr(worker.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(worker.settings, "telegram_chat_id", "chat")

    result = await worker._run_once_locked()

    assert events.count("commit") == 2
    assert events[100] == "commit"
    assert events[103:] == ["commit", "send"]
    assert result["checked"] == 51
    assert worker.read_monitor_status()["batch_current"] == 2
    assert worker.read_monitor_status()["batch_total"] == 2
    assert worker.read_monitor_status()["checked"] == 51
    assert result["alerts"] == 51
    assert result["messages"] == 1
    assert result["send_failed"] == 1
    assert len(sent_messages) == 1
    assert "📌 Tổng affected: <b>51</b>" in sent_messages[0]
    assert len(ping_calls) == 102
    assert all(call[2] == 5 for call in ping_calls)

    db = session_factory()
    assert db.query(Incident).count() == 51
    assert {incident.id for incident in db.query(Incident).all()} == set(range(1, 52))
    db.close()
