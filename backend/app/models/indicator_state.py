"""指标增量状态持久化：重启免全量 bootstrap。"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IndicatorState(Base):
    __tablename__ = "indicator_state"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )
