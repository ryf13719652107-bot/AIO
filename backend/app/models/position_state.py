from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PositionState(Base):
    """每个币种最新的持仓与策略状态（两级加仓 + 退出基准）。"""

    __tablename__ = "position_state"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    direction: Mapped[str] = mapped_column(String(8))                # FLAT / LONG / SHORT
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    margin: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    add_count: Mapped[int] = mapped_column(Integer, default=0)
    add_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline_armed: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline_price: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_open_ms: Mapped[int] = mapped_column(Integer, default=0)
    peak_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
