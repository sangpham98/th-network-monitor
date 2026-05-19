from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    store_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    pc_name: Mapped[str | None] = mapped_column(String(64))
    ip_local: Mapped[str | None] = mapped_column(String(64))
    ip_tunnel: Mapped[str | None] = mapped_column(String(64), index=True)
    wan_dns: Mapped[str | None] = mapped_column(String(255), index=True)
    region: Mapped[str | None] = mapped_column(String(64), index=True)
    area: Mapped[str | None] = mapped_column(String(128), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    status = relationship("StoreStatus", back_populates="store", uselist=False, cascade="all, delete-orphan")


class StoreStatus(Base):
    __tablename__ = "store_status"

    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    wan_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    tunnel_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    overall_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN", index=True)
    wan_fail_count: Mapped[int] = mapped_column(Integer, default=0)
    tunnel_fail_count: Mapped[int] = mapped_column(Integer, default=0)
    wan_success_count: Mapped[int] = mapped_column(Integer, default=0)
    tunnel_success_count: Mapped[int] = mapped_column(Integer, default=0)
    wan_down_window: Mapped[str] = mapped_column(Text, default="")
    tunnel_down_window: Mapped[str] = mapped_column(Text, default="")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime)

    store = relationship("Store", back_populates="status")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    incident_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    alert_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)
