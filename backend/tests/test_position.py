"""仓位状态机测试：开仓/加仓档/TP6 分批/保本损/持久化序列化。"""

from app.strategy.position import (
    LEG_FIRST, SymbolPosition, TP6_KEYS,
)


def test_open_and_state():
    pos = SymbolPosition(symbol="BTCUSDT")
    assert pos.is_flat
    pos.open("LONG", 1.0, 100.0, 10.0, params_snapshot={"a": 1})
    assert pos.direction == "LONG"
    assert pos.quantity == 1.0
    assert pos.entry_price == 100.0
    assert pos.margin == 10.0
    assert pos.orders_count == 1
    assert LEG_FIRST in pos.legs_fired
    assert pos.params_snapshot == {"a": 1}
    assert not pos.breakeven_armed


def test_add_updates_weighted_average_and_marks_leg():
    pos = SymbolPosition(symbol="ETHUSDT")
    pos.open("LONG", 1.0, 100.0, 10.0)
    pos.add(1.0, 120.0, 10.0, "ema7")
    assert math_close(pos.entry_price, 110.0)
    assert pos.quantity == 2.0
    assert pos.margin == 20.0
    assert pos.orders_count == 2
    assert pos.is_leg_fired("ema7")
    # 同档不能重复加
    pos.add(1.0, 130.0, 10.0, "ema7")
    assert pos.orders_count == 2


def test_pullback_armed_state():
    pos = SymbolPosition(symbol="XRPUSDT")
    pos.open("LONG", 1.0, 100.0, 10.0)
    pos.set_pullback_armed("ema7", True)
    assert pos.get_pullback_armed("ema7")
    assert not pos.get_pullback_armed("ema25")


def test_tp6_fire_arms_breakeven_once_per_level():
    pos = SymbolPosition(symbol="SOLUSDT")
    pos.open("SHORT", 2.0, 50.0, 5.0)
    pos.fire_tp6("t5m")
    assert pos.is_tp6_fired("t5m")
    assert pos.breakeven_armed
    assert not pos.is_tp6_fired("t15m")


def test_partial_close_proportional():
    pos = SymbolPosition(symbol="BTCUSDT")
    pos.open("LONG", 2.0, 100.0, 20.0)
    qty = pos.partial_close(10.0)
    assert math_close(qty, 0.2)
    assert math_close(pos.quantity, 1.8)
    assert math_close(pos.margin, 18.0)


def test_tp6_ladder_uses_initial_total_and_sums_to_full():
    """TP6 各档按"该笔订单总仓位"百分比（文档 L25-29），5 档合计正好平完。"""
    pos = SymbolPosition(symbol="BTCUSDT")
    pos.open("LONG", 5.0, 100.0, 5.0)
    pos.add(5.0, 110.0, 5.0, "ema7")     # 总仓 10
    assert math_close(pos.initial_quantity, 10.0)
    total = pos.initial_quantity
    closed = 0.0
    for key, pct in (("t5m", 10.0), ("t15m", 20.0), ("t30m", 20.0),
                     ("t60m", 30.0), ("t60m_120m", 20.0)):
        pos.fire_tp6(key)
        # 模拟引擎 _do_tp6 的换算
        close_qty = min(total * pct / 100.0, pos.quantity)
        ratio = close_qty / pos.quantity if pos.quantity > 0 else 0.0
        pos.partial_close(ratio * 100.0)
        closed += close_qty
    assert math_close(closed, 10.0)
    assert math_close(pos.quantity, 0.0)


def test_initial_quantity_roundtrip_and_legacy_fallback():
    pos = SymbolPosition(symbol="BTCUSDT")
    pos.open("LONG", 3.0, 100.0, 3.0)
    pos.add(2.0, 105.0, 2.0, "ema7")     # 总仓 5
    pos2 = SymbolPosition.from_dict(pos.to_dict())
    assert math_close(pos2.initial_quantity, 5.0)
    # 旧持久化数据无 initial_quantity → 用 quantity 兜底
    legacy = pos.to_dict()
    legacy.pop("initial_quantity")
    pos3 = SymbolPosition.from_dict(legacy)
    assert math_close(pos3.initial_quantity, 5.0)


def test_breakeven_pnl_directions():
    pos = SymbolPosition(symbol="BTCUSDT")
    pos.open("LONG", 1.0, 100.0, 10.0)
    assert math_close(pos.unrealized_pnl(110), 10.0)
    assert math_close(pos.unrealized_pnl(90), -10.0)

    short = SymbolPosition(symbol="BTCUSDT")
    short.open("SHORT", 1.0, 100.0, 10.0)
    assert math_close(short.unrealized_pnl(90), 10.0)
    assert math_close(short.unrealized_pnl(110), -10.0)


def test_adopt_blocks_adds_and_marks_legs_used():
    pos = SymbolPosition(symbol="ADAUSDT")
    pos.adopt("SHORT", 3.0, 0.5, 2.0, block_add=True)
    assert pos.direction == "SHORT"
    assert pos.add_blocked
    assert pos.is_leg_fired("ema7")
    assert pos.orders_count == 1


def test_round_trip_dict():
    pos = SymbolPosition(symbol="DOGEUSDT")
    pos.open("LONG", 1.5, 0.2, 3.0, params_snapshot={"order_plan": {}})
    pos.add(0.5, 0.21, 1.0, "t15m")
    pos.fire_tp6("t5m")
    pos.set_pullback_armed("ema25", True)
    d = pos.to_dict()
    pos2 = SymbolPosition.from_dict(d)
    assert pos2.direction == "LONG"
    assert math_close(pos2.entry_price, pos.entry_price)
    assert pos2.orders_count == 2
    assert pos2.is_leg_fired("t15m")
    assert pos2.is_tp6_fired("t5m")
    assert pos2.breakeven_armed
    assert pos2.get_pullback_armed("ema25")


def test_close_resets_all():
    pos = SymbolPosition(symbol="BNBUSDT")
    pos.open("LONG", 1.0, 600.0, 10.0)
    pos.fire_tp6("t60m")
    pos.close()
    assert pos.is_flat
    assert not pos.legs_fired
    assert not pos.tp6_fired
    assert not pos.breakeven_armed
    assert pos.orders_count == 0
    assert TP6_KEYS  # 常量存在


def math_close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol
