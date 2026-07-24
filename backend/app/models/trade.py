from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Trade(Base):
    """已成交订单流水。"""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(8))           # BUY / SELL
    position_side: Mapped[str] = mapped_column(String(8))  # LONG / SHORT
    event: Mapped[str] = mapped_column(String(24))         # OPEN / ADD_1..4 / CLOSE_TP / CLOSE_SL
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)         # = quantity * price
    margin: Mapped[float] = mapped_column(Float)           # 实际占用保证金
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PositionLog(Base):
    """关键事件日志（开仓/补仓/止盈/止损/熔断/错误）。"""

    __tablename__ = "position_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    level: Mapped[str] = mapped_column(String(8))          # INFO / WARN / ERROR
    event: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
