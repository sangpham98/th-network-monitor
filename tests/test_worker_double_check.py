import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Store
from monitor import worker


@pytest.mark.asyncio
async def test_alert_send_does_not_recheck_targets(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    setup_db = session_factory()
    setup_db.add(Store(store_code="70000123", pc_name="PC001", wan_dns="wan.example", ip_tunnel="10.0.0.1"))
    setup_db.commit()
    setup_db.close()
    calls = []
    sent_messages = []

    async def check_wan(target, _timeout, retry):
        calls.append(("wan", target, retry))
        return False

    async def ping_host(target, _timeout, retry):
        calls.append(("tunnel", target, retry))
        return True

    async def send_telegram(message):
        sent_messages.append(message)
        return False

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "STATUS_PATH", tmp_path / "monitor_status.json")
    monkeypatch.setattr(worker, "check_wan", check_wan)
    monkeypatch.setattr(worker, "ping_host", ping_host)
    monkeypatch.setattr(worker, "send_telegram", send_telegram)
    monkeypatch.setattr(worker.settings, "telegram_bot_token", "token")
    monkeypatch.setattr(worker.settings, "telegram_chat_id", "chat")

    first_result = await worker._run_once_locked()
    second_result = await worker._run_once_locked()

    assert first_result["alerts"] == 0
    assert first_result["messages"] == 0
    assert second_result["alerts"] == 1
    assert second_result["messages"] == 1
    assert sent_messages
    assert calls == [
        ("wan", "wan.example", 5),
        ("tunnel", "10.0.0.1", 5),
        ("wan", "wan.example", 5),
        ("tunnel", "10.0.0.1", 5),
    ]
