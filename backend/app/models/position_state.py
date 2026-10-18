from datetime import datetime
from typing import Mapped, Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import mapped_column

from app.database import Base


class PositionState(Base):
    """每个币种每个方向的持仓状态（双向持仓，state_json 全量序列化）。"""

    __tablename__ = "position_state"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    direction: Mapped[str] = mapped_column(String(8), primary_key=True)  # LONG / SHORT
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
