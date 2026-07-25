"""
单币种仓位状态机。
- 首仓 + 两级顺序加仓（各最多一次）
- 开/加仓后等待下一根 1m K 收盘开始移动止盈
- 武装后按峰值浮盈 peak_pnl 回撤（真正移动止盈）；SL2 等待期也生效
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

Direction = Literal["FLAT", "LONG", "SHORT"]


@dataclass
class SymbolPosition:
    symbol: str
    direction: Direction = "FLAT"

    quantity: float = 0.0
    entry_price: float = 0.0
    margin: float = 0.0
    opened_at: Optional[datetime] = None
    mark_price: float = 0.0

    # 加仓：0=未加，1=已加一级，2=已加二级（或接管仓禁止再加）
    add_count: int = 0
    add_blocked: bool = False  # 交易所接管仓：禁止加仓

    # 退出基准
    pending_baseline: bool = False      # 等待下一根 1m K 收盘开始 TP1
    pending_baseline_since_ms: int = 0  # 开始等待的时刻（ms）
    baseline_armed: bool = False
    baseline_price: float = 0.0         # 武装时参考价（1m 收盘价）
    baseline_pnl: float = 0.0           # 武装时浮盈（可≤0）；展示用
    baseline_open_ms: int = 0           # 武装所用 1m K 的 open_ms
    peak_pnl: float = 0.0               # 武装后见到的最高浮盈（移动止盈峰值）

    @property
    def is_flat(self) -> bool:
        return self.direction == "FLAT"

    @property
    def avg_price(self) -> float:
        return self.entry_price

    @property
    def add_fired(self) -> bool:
        """兼容旧代码：是否至少加过一次。"""
        return self.add_count >= 1

    @property
    def add_conditions_fired(self) -> set[str]:
        keys = set()
        if self.add_count >= 1:
            keys.add("add1")
        if self.add_count >= 2:
            keys.add("add2")
        return keys

    def can_add_level(self, level: int) -> bool:
        if self.is_flat or self.add_blocked:
            return False
        if level == 1:
            return self.add_count == 0
        if level == 2:
            return self.add_count == 1
        return False

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

    def _reset_baseline_wait(self, since_ms: Optional[int] = None):
        self.pending_baseline = True
        self.pending_baseline_since_ms = int(
            since_ms if since_ms is not None else time.time() * 1000
        )
        self.baseline_armed = False
        self.baseline_price = 0.0
        self.baseline_pnl = 0.0
        self.baseline_open_ms = 0
        self.peak_pnl = 0.0

    def open(self, side: Direction, quantity: float, price: float, margin: float,
             *, since_ms: Optional[int] = None):
        assert self.direction == "FLAT", "已有持仓不能开新仓"
        self.direction = side
        self.quantity = quantity
        self.entry_price = price
        self.margin = margin
        self.opened_at = datetime.utcnow()
        self.add_count = 0
        self.add_blocked = False
        self.mark_price = price
        self._reset_baseline_wait(since_ms)

    def add(self, quantity: float, price: float, margin: float, trigger_key: str = "",
            *, since_ms: Optional[int] = None):
        """加仓：更新加权均价与累计保证金，并重置退出基准等待。"""
        if self.is_flat or self.add_blocked:
            return
        if self.add_count >= 2:
            return
        total_qty = self.quantity + quantity
        if total_qty <= 0:
            return
        self.entry_price = (self.entry_price * self.quantity + price * quantity) / total_qty
        self.quantity = total_qty
        self.margin += margin
        self.add_count = min(2, self.add_count + 1)
        self._reset_baseline_wait(since_ms)

    def adopt(self, side: Direction, quantity: float, price: float, margin: float,
              *, block_add: bool = True, since_ms: Optional[int] = None):
        """接管交易所已有仓：禁止加仓，等待下一根 1m K 收盘建立基准。"""
        self.direction = side
        self.quantity = quantity
        self.entry_price = price
        self.margin = margin
        self.opened_at = self.opened_at or datetime.utcnow()
        self.mark_price = price
        self.add_count = 2 if block_add else self.add_count
        self.add_blocked = block_add
        self._reset_baseline_wait(since_ms)

    def arm_baseline(self, ref_price: float, open_ms: int = 0,
                     *, close_boundary_ms: int = 0) -> bool:
        """下一根 1m K 收盘后开始移动止盈：初始化峰值。"""
        if self.is_flat or not self.pending_baseline:
            return False
        # 必须是进入等待之后的收盘，避免开仓当根立刻武装
        if close_boundary_ms and self.pending_baseline_since_ms:
            if close_boundary_ms <= self.pending_baseline_since_ms:
                return False
        self.mark_price = ref_price
        self.baseline_price = ref_price
        pnl = self.unrealized_pnl(ref_price)
        self.baseline_pnl = pnl
        # 峰值至少从 0 起：武装时若已亏损，等后续浮盈出现再移动止盈
        self.peak_pnl = max(0.0, pnl)
        self.baseline_open_ms = open_ms
        self.baseline_armed = True
        self.pending_baseline = False
        self.pending_baseline_since_ms = 0
        return True

    def ratchet_peak(self, mark: float) -> float:
        """武装后上移峰值浮盈；返回当前峰值。"""
        if self.is_flat or not self.baseline_armed or mark <= 0:
            return self.peak_pnl
        self.mark_price = mark
        pnl = self.unrealized_pnl(mark)
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl
            self.baseline_price = mark
        return self.peak_pnl

    def close(self):
        self.direction = "FLAT"
        self.quantity = 0.0
        self.entry_price = 0.0
        self.margin = 0.0
        self.opened_at = None
        self.add_count = 0
        self.add_blocked = False
        self.pending_baseline = False
        self.pending_baseline_since_ms = 0
        self.baseline_armed = False
        self.baseline_price = 0.0
        self.baseline_pnl = 0.0
        self.baseline_open_ms = 0
        self.peak_pnl = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "margin": self.margin,
            "opened_at_iso": self.opened_at.isoformat() if self.opened_at else None,
            "add_count": self.add_count,
            "add_blocked": self.add_blocked,
            "pending_baseline": self.pending_baseline,
            "pending_baseline_since_ms": self.pending_baseline_since_ms,
            "baseline_armed": self.baseline_armed,
            "baseline_price": self.baseline_price,
            "baseline_pnl": self.baseline_pnl,
            "baseline_open_ms": self.baseline_open_ms,
            "peak_pnl": self.peak_pnl,
            "mark_price": self.mark_price,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolPosition":
        pos = cls(symbol=d["symbol"])
        pos.direction = d.get("direction", "FLAT")
        pos.quantity = float(d.get("quantity", 0))
        pos.entry_price = float(d.get("entry_price", 0))
        pos.margin = float(d.get("margin", 0))
        oa = d.get("opened_at_iso")
        if oa:
            pos.opened_at = datetime.fromisoformat(oa)
        if "add_count" in d:
            pos.add_count = int(d.get("add_count", 0))
        elif d.get("add_fired"):
            pos.add_count = 1
        pos.add_blocked = bool(d.get("add_blocked", False))
        pos.pending_baseline = bool(d.get("pending_baseline", False))
        pos.pending_baseline_since_ms = int(d.get("pending_baseline_since_ms", 0) or 0)
        pos.baseline_armed = bool(d.get("baseline_armed", False))
        pos.baseline_price = float(d.get("baseline_price", 0) or 0)
        pos.baseline_pnl = float(d.get("baseline_pnl", 0) or 0)
        pos.baseline_open_ms = int(d.get("baseline_open_ms", 0) or 0)
        # 兼容旧状态：无 peak 时用 max(0, baseline_pnl)
        if "peak_pnl" in d:
            pos.peak_pnl = float(d.get("peak_pnl", 0) or 0)
        else:
            pos.peak_pnl = max(0.0, pos.baseline_pnl) if pos.baseline_armed else 0.0
        pos.mark_price = float(d.get("mark_price", 0) or 0)
        return pos

    def snapshot(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "margin_used": self.margin,
            "add_count": self.add_count,
            "add_fired": self.add_count >= 1,
            "add_blocked": self.add_blocked,
            "pending_baseline": self.pending_baseline,
            "baseline_armed": self.baseline_armed,
            "baseline_price": self.baseline_price,
            "baseline_pnl": self.baseline_pnl,
            "peak_pnl": self.peak_pnl,
            "unrealized_pnl": self.unrealized_pnl(),
            "unrealized_pnl_ratio": self.unrealized_pnl_ratio(),
            "mark_price": self.mark_price,
        }
