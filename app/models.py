from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class Creation(Base):
    __tablename__ = "creations"
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    alias: Mapped[str] = mapped_column(String(64), unique=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Draft(Base):
    __tablename__ = "drafts"
    stream_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    baseline: Mapped[dict] = mapped_column(JSON)
    offers: Mapped[list] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
