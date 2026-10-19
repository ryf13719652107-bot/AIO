"""
单币种单方向仓位状态机（双向持仓的一腿）。

- 首仓 + 5 个加仓档（ema7/ema25/ema99 回踩 + t15m/t30m 趋势），各档仅一次
- 回踩档武装状态（先站上 EMA 再回踩才触发）
- TP6 分批止盈（各档仅一次），任一档触发后武装保本损
- 开仓时快照 StrategyParams，旧仓按快照策略管理
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

Direction = Literal["FLAT", "LONG", "SHORT"]

# 加仓档触发 key
LEG_FIRST = "first"
LEG_KEYS = ("ema7", "ema25", "ema99", "t15m", "t30m")
TP6_KEYS = ("t5m", "t15m", "t30m", "t60m", "t60m_120m")


@dataclass
class SymbolPosition:
    symbol: str
    direction: Direction = "FLAT"

    quantity: float = 0.0
    initial_quantity: float = 0.0  # 该笔订单总仓位（TP6 分批比例的分母）
    entry_price: float = 0.0      # 加权开仓均价（保本损基准）
    margin: float = 0.0           # 累计保证金
    opened_at: Optional[datetime] = None
    mark_price: float = 0.0

    orders_count: int = 0                        # 已开单数（含首仓）
    legs_fired: set = field(default_factory=set)  # 已触发档 key
    pullback_armed: dict = field(default_factory=dict)  # key→bool 回踩武装
    tp6_fired: set = field(default_factory=set)   # 已触发 TP6 档 key
    breakeven_armed: bool = False                 # 保本损武装（TP6 触发后）
    params_snapshot: dict = field(default_factory=dict)  # 开仓时策略参数快照
    add_blocked: bool = False                     # 接管仓：禁止加仓

    @property
    def is_flat(self) -> bool:
        return self.direction == "FLAT"

    @property
    def avg_price(self) -> float:
        return self.entry_price

    def is_leg_fired(self, key: str) -> bool:
        return key in self.legs_fired

    def mark_leg_fired(self, key: str):
        self.legs_fired.add(key)

    def get_pullback_armed(self, key: str) -> bool:
        return bool(self.pullback_armed.get(key, False))

    def set_pullback_armed(self, key: str, armed: bool):
        self.pullback_armed[key] = armed

    def is_tp6_fired(self, key: str) -> bool:
        return key in self.tp6_fired

    def unrealized_pnl(self, mark: Optional[float] = None) -> float:
        if self.is_flat:
            return 0.0
        price = mark if mark is not None else self.mark_price
        if not price:
            return 0.0
        if self.direction == "LONG":
            return (price - self.entry_price) * self.quantity
        return (self.entry_price - price) * self.quantity

    def unrealized_pnl_ratio(self, mark: Optional[float] = None) -> float:
        if self.margin <= 0:
            return 0.0
        return self.unrealized_pnl(mark) / self.margin

    def open(self, side: Direction, quantity: float, price: float, margin: float,
             params_snapshot: Optional[dict] = None, leg_key: str = LEG_FIRST):
        assert self.direction == "FLAT", "该方向已有持仓"
        self.direction = side
        self.quantity = quantity
        self.initial_quantity = quantity
        self.entry_price = price
        self.margin = margin
        self.opened_at = datetime.utcnow()
        self.orders_count = 1
        self.mark_price = price
        self.params_snapshot = params_snapshot or {}
        self.legs_fired = {leg_key}
        self.pullback_armed = {}
        self.tp6_fired = set()
        self.breakeven_armed = False

    def add(self, quantity: float, price: float, margin: float, leg_key: str):
        """加仓：更新加权均价与累计保证金，标记该档已触发。"""
        if self.is_flat or self.add_blocked:
            return
        if leg_key in self.legs_fired:
            return
        total_qty = self.quantity + quantity
        if total_qty <= 0:
            return
        self.entry_price = (
            (self.entry_price * self.quantity + price * quantity) / total_qty
        )
        self.quantity = total_qty
        self.initial_quantity += quantity
        self.margin += margin
        self.orders_count += 1
        self.mark_leg_fired(leg_key)

    def partial_close(self, pct: float) -> float:
        """TP6 分批：按 pct% 减仓（数量与保证金等比例缩减）。返回减仓数量。"""
        if self.is_flat or pct <= 0:
            return 0.0
        pct = min(pct, 100.0)
        qty = self.quantity * (pct / 100.0)
        self.quantity = max(0.0, self.quantity - qty)
        self.margin = max(0.0, self.margin * (1.0 - pct / 100.0))
        return qty

    def adopt(self, side: Direction, quantity: float, price: float, margin: float,
              *, block_add: bool = True, params_snapshot: Optional[dict] = None):
        """接管交易所已有仓：默认禁止加仓（无档位记录），TP 正常执行。"""
        self.direction = side
        self.quantity = quantity
        self.initial_quantity = quantity
        self.entry_price = price
        self.margin = margin
        self.opened_at = self.opened_at or datetime.utcnow()
        self.mark_price = price
        self.orders_count = 1
        self.legs_fired = set(LEG_KEYS) | {LEG_FIRST}  # 视为全部档已用
        self.pullback_armed = {}
        self.tp6_fired = set()
        self.breakeven_armed = False
        self.add_blocked = block_add
        if params_snapshot:
            self.params_snapshot = params_snapshot

    def fire_tp6(self, key: str):
        self.tp6_fired.add(key)
        self.breakeven_armed = True

    def undo_fire_tp6(self, key: str):
        """下单失败回滚：撤销该档标记；若无任何档实际成交则一并撤销保本损武装。"""
        self.tp6_fired.discard(key)
        if not self.tp6_fired:
            self.breakeven_armed = False

    def close(self):
        self.direction = "FLAT"
        self.quantity = 0.0
        self.initial_quantity = 0.0
        self.entry_price = 0.0
        self.margin = 0.0
        self.opened_at = None
        self.orders_count = 0
        self.legs_fired = set()
        self.pullback_armed = {}
        self.tp6_fired = set()
        self.breakeven_armed = False
        self.params_snapshot = {}
        self.add_blocked = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "quantity": self.quantity,
            "initial_quantity": self.initial_quantity,
            "entry_price": self.entry_price,
            "margin": self.margin,
            "opened_at_iso": self.opened_at.isoformat() if self.opened_at else None,
            "orders_count": self.orders_count,
            "legs_fired": sorted(self.legs_fired),
            "pullback_armed": {k: bool(v) for k, v in self.pullback_armed.items()},
            "tp6_fired": sorted(self.tp6_fired),
            "breakeven_armed": self.breakeven_armed,
            "params_snapshot": self.params_snapshot,
            "add_blocked": self.add_blocked,
            "mark_price": self.mark_price,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolPosition":
        pos = cls(symbol=d.get("symbol", ""))
        pos.direction = d.get("direction", "FLAT")
        pos.quantity = float(d.get("quantity", 0.0))
        # 旧持久化数据无 initial_quantity → 用当前 quantity 兜底
        pos.initial_quantity = float(d.get("initial_quantity") or d.get("quantity", 0.0))
        pos.entry_price = float(d.get("entry_price", 0.0))
        pos.margin = float(d.get("margin", 0.0))
        iso = d.get("opened_at_iso")
        try:
            pos.opened_at = datetime.fromisoformat(iso) if iso else None
        except (ValueError, TypeError):
            pos.opened_at = None
        pos.orders_count = int(d.get("orders_count", 0))
        pos.legs_fired = set(d.get("legs_fired") or ())
        pos.pullback_armed = dict(d.get("pullback_armed") or {})
        pos.tp6_fired = set(d.get("tp6_fired") or ())
        pos.breakeven_armed = bool(d.get("breakeven_armed", False))
        pos.params_snapshot = dict(d.get("params_snapshot") or {})
        pos.add_blocked = bool(d.get("add_blocked", False))
        pos.mark_price = float(d.get("mark_price", 0.0))
        return pos

    def snapshot(self, mark: Optional[float] = None) -> dict:
        m = mark if mark is not None else self.mark_price
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "margin": self.margin,
            "margin_used": self.margin,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "orders_count": self.orders_count,
            "legs_fired": sorted(self.legs_fired),
            "pullback_armed": {k: bool(v) for k, v in self.pullback_armed.items()},
            "tp6_fired": sorted(self.tp6_fired),
            "breakeven_armed": self.breakeven_armed,
            "unrealized_pnl": self.unrealized_pnl(m),
            "unrealized_pnl_ratio": self.unrealized_pnl_ratio(m),
            "mark_price": m,
        }
