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
async def test_run_once_commits_status_updates_per_configured_batch(monkeypatch):
    session_factory, session_local, events = make_session_factory()
    setup_db = session_factory()
    add_stores(setup_db, 120)
    setup_db.close()
    events.clear()

    async def check_wan(_target, _timeout, _retry):
        return True

    async def ping_host(_target, _timeout, _retry):
        return True

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    monkeypatch.setattr(worker.settings, "max_concurrency", 50)
    monkeypatch.setattr(worker.settings, "up_threshold", 1)
    monkeypatch.setattr(worker.settings, "telegram_bot_token", "")
    monkeypatch.setattr(worker.settings, "telegram_chat_id", "")

    result = await worker._run_once_locked()

    assert result["checked"] == 120
    assert events == ["commit", "commit", "commit"]

    db = session_factory()
    assert db.query(StoreStatus).count() == 120
    db.close()


@pytest.mark.asyncio
async def test_run_once_sends_telegram_after_all_status_batches(monkeypatch):
    session_factory, session_local, events = make_session_factory()
    setup_db = session_factory()
    add_stores(setup_db, 51)
    setup_db.close()
    events.clear()
    sent_messages = []

    async def check_wan(_target, _timeout, _retry):
        return False

    async def ping_host(_target, _timeout, _retry):
        return True

    async def send_telegram(message):
        events.append("send")
        sent_messages.append(message)
        return False

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    monkeypatch.setattr(worker, "send_telegram", send_telegram)
    monkeypatch.setattr(worker.settings, "max_concurrency", 50)
    monkeypatch.setattr(worker.settings, "down_threshold", 1)
    monkeypatch.setattr(worker.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(worker.settings, "telegram_chat_id", "chat")

    result = await worker._run_once_locked()

    assert events == ["commit", "commit", "send"]
    assert result["checked"] == 51
    assert result["alerts"] == 51
    assert result["messages"] == 1
    assert result["send_failed"] == 1
    assert len(sent_messages) == 1
    assert "Tổng affected: 51" in sent_messages[0]

    db = session_factory()
    assert db.query(Incident).count() == 51
    assert {incident.id for incident in db.query(Incident).all()} == set(range(1, 52))
    db.close()
