"""仓位两级加仓与退出基准。"""

from app.strategy.position import SymbolPosition


def test_add_two_levels_sequential():
    pos = SymbolPosition(symbol="BTCUSDT")
    pos.open("LONG", quantity=1.0, price=100.0, margin=10.0)
    assert pos.can_add_level(1)
    assert not pos.can_add_level(2)
    assert pos.pending_baseline is True
    assert pos.baseline_armed is False

    pos.add(1.0, 120.0, 10.0, "add1")
    assert pos.add_count == 1
    assert pos.entry_price == 110.0
    assert pos.margin == 20.0
    assert pos.can_add_level(2)
    assert pos.pending_baseline is True

    pos.add(1.0, 130.0, 10.0, "add2")
    assert pos.add_count == 2
    assert not pos.can_add_level(1)
    assert not pos.can_add_level(2)


def test_arm_baseline_fixed_pnl():
    pos = SymbolPosition(symbol="ETHUSDT")
    pos.open("LONG", 1.0, 100.0, 10.0)
    assert pos.arm_baseline(110.0, open_ms=123) is True
    assert pos.baseline_armed is True
    assert pos.pending_baseline is False
    assert pos.baseline_price == 110.0
    assert abs(pos.baseline_pnl - 10.0) < 1e-9  # (110-100)*1


def test_arm_baseline_waits_for_later_1m_close():
    pos = SymbolPosition(symbol="BTCUSDT")
    pos.open("LONG", 1.0, 100.0, 10.0, since_ms=1_000_000)
    # 收盘边界早于等待开始 → 不武装
    assert pos.arm_baseline(110.0, open_ms=900_000, close_boundary_ms=960_000) is False
    assert pos.pending_baseline is True
    # 开仓之后的 1m 收盘 → 武装
    assert pos.arm_baseline(110.0, open_ms=1_000_000, close_boundary_ms=1_060_000) is True
    assert pos.baseline_armed is True
    assert abs(pos.baseline_pnl - 10.0) < 1e-9


def test_adopt_blocks_add():
    pos = SymbolPosition(symbol="XRPUSDT")
    pos.adopt("SHORT", 100.0, 0.5, 5.0, block_add=True)
    assert pos.add_count == 2
    assert pos.add_blocked is True
    assert not pos.can_add_level(1)
    assert pos.pending_baseline is True


def test_add_resets_baseline():
    pos = SymbolPosition(symbol="SOLUSDT")
    pos.open("LONG", 1.0, 100.0, 10.0)
    pos.arm_baseline(105.0, 1)
    assert pos.baseline_armed
    pos.add(1.0, 110.0, 10.0, "add1")
    assert pos.pending_baseline is True
    assert pos.baseline_armed is False
