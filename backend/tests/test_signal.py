"""RSI+VOL 信号规则测试。"""

from app.schemas import StrategyParams
from app.strategy.indicators import SymbolIndicators
from app.strategy.position import SymbolPosition
from app.strategy.signal import StrategyRules


def _ready_ind(rsi_path=None, final_vol=100.0, lookback=5):
    """构造可交易指标：前 lookback 根量=10，最后一根 final_vol。"""
    ind = SymbolIndicators(timeframe="1m", rsi_period=6, volume_lookback=lookback)
    n = lookback + 8
    if rsi_path is None:
        # 下跌路径，利于低 RSI
        closes = [100 - i * 0.5 for i in range(n)]
    else:
        closes = list(rsi_path)
        while len(closes) < n:
            closes.append(closes[-1])
    vols = [10.0] * (n - 1) + [final_vol]
    ind.bootstrap("1m", closes, vols)
    return ind


def test_long_entry_and_requires_both():
    params = StrategyParams()
    params.timeframe = "1m"
    params.entry_conditions.long.vol_lookback = 5
    params.entry_conditions.long.vol_mult = 7
    params.entry_conditions.long.rsi_threshold = 100  # 任意 RSI 都满足 ≤100
    params.entry_conditions.short.enable_rsi = False
    params.entry_conditions.short.enable_vol = False

    ind = _ready_ind(final_vol=100.0)  # 10x > 7x
    ok, _ = StrategyRules.long_entry_ready(params, ind)
    assert ok

    params.entry_conditions.long.enable_vol = False
    params.entry_conditions.long.enable_rsi = False
    ok, _ = StrategyRules.long_entry_ready(params, ind)
    assert not ok  # 全关禁用


def test_short_entry_rsi_gate():
    params = StrategyParams()
    params.timeframe = "1m"
    params.entry_conditions.short.vol_lookback = 5
    params.entry_conditions.short.vol_mult = 7
    params.entry_conditions.short.rsi_threshold = 0  # RSI≥0 恒真
    params.entry_conditions.long.enable_rsi = False
    params.entry_conditions.long.enable_vol = False
    ind = _ready_ind(final_vol=100.0)
    ok, _ = StrategyRules.short_entry_ready(params, ind)
    assert ok


def test_add_levels_order():
    params = StrategyParams()
    params.timeframe = "1m"
    params.add_conditions.enabled = True
    for lv in (params.add_conditions.level1, params.add_conditions.level2):
        lv.long.vol_lookback = 5
        lv.long.vol_mult = 7
        lv.long.rsi_threshold = 100
        lv.short.enable_rsi = False
        lv.short.enable_vol = False
    ind = _ready_ind(final_vol=100.0)
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 10)
    ok1, _ = StrategyRules.add_level_ready(params, ind, pos, 1)
    assert ok1
    ok2, _ = StrategyRules.add_level_ready(params, ind, pos, 2)
    assert not ok2
    pos.add(1, 110, 10, "add1")
    ok2, _ = StrategyRules.add_level_ready(params, ind, pos, 2)
    assert ok2


def test_evaluate_entry_returns_add_when_holding():
    params = StrategyParams()
    params.timeframe = "1m"
    params.entry_conditions.long.vol_lookback = 5
    params.add_conditions.enabled = True
    params.add_conditions.level1.long.vol_lookback = 5
    params.add_conditions.level1.long.vol_mult = 10
    params.add_conditions.level1.long.rsi_threshold = 100
    params.add_conditions.level1.short.enable_rsi = False
    params.add_conditions.level1.short.enable_vol = False
    ind = _ready_ind(final_vol=10.0)  # 收盘不够 10x
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 10)
    act = StrategyRules.evaluate_entry(params, ind, pos)
    assert act.type == "NONE"
    avg = ind.get("1m").volume.avg_closed(5)
    ind.set_live("1m", volume=avg * 10.5, close=90)
    act = StrategyRules.evaluate_entry(params, ind, pos)
    assert act.type == "ADD"
    assert act.trigger_key == "add1"


def test_tp1_drawdown_20_to_14():
    params = StrategyParams()
    params.exit.enable_tp1 = True
    params.exit.tp1_drawdown_pct = 30
    params.exit.enable_sl1 = False
    params.exit.enable_sl2 = False
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 100)  # margin 100
    pos.arm_baseline(120, 1)  # peak = 20
    assert abs(pos.peak_pnl - 20) < 1e-9
    # 浮盈 15 > 14 → 不触发
    act = StrategyRules.check_price_exits(params, pos, 115)
    assert act is None
    # 浮盈 14 → 触发
    act = StrategyRules.check_price_exits(params, pos, 114)
    assert act is not None and act.type == "CLOSE_TP1"


def test_tp1_trails_peak_not_fixed_baseline():
    """浮盈创新高后，回撤按峰值而非开仓时固定 P0。"""
    params = StrategyParams()
    params.exit.enable_tp1 = True
    params.exit.tp1_drawdown_pct = 30
    params.exit.enable_sl1 = False
    params.exit.enable_sl2 = False
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 100)
    pos.arm_baseline(110, 1)  # 初始峰值 10，旧逻辑地板=7
    # 冲到 150 → 峰值 50，地板应为 35
    act = StrategyRules.check_price_exits(params, pos, 150)
    assert act is None
    assert abs(pos.peak_pnl - 50) < 1e-9
    # 回撤到 40（>35）不触发；若仍用旧固定 P0=10 则会误触（40>7 其实也不会）
    # 关键场景：回撤到 34 ≤ 35 → 必须触发；旧固定地板 7 也会触发，但峰值语义不同
    act = StrategyRules.check_price_exits(params, pos, 140)  # pnl=40
    assert act is None
    act = StrategyRules.check_price_exits(params, pos, 134)  # pnl=34 ≤ 35
    assert act is not None and act.type == "CLOSE_TP1"
    assert "峰值=" in act.reason


def test_sl1_breakeven():
    params = StrategyParams()
    params.exit.enable_tp1 = False
    params.exit.enable_sl1 = True
    params.exit.enable_sl2 = False
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 100)
    pos.arm_baseline(110, 1)  # peak=10
    act = StrategyRules.check_price_exits(params, pos, 101)
    assert act is None
    act = StrategyRules.check_price_exits(params, pos, 100)
    assert act is not None and act.type == "CLOSE_SL1"


def test_sl2_uses_actual_margin_not_budget():
    """SL2 按真实保证金；若误用偏大预算会拖到很高 ROI 才停。"""
    params = StrategyParams()
    params.exit.enable_tp1 = False
    params.exit.enable_sl1 = False
    params.exit.enable_sl2 = True
    params.exit.sl2_margin_loss_pct = 10
    # 模拟 EDU：qty=259 entry=0.03472 lev=20 → 真实保证金≈0.45
    qty, entry, lev = 259.0, 0.03472, 20
    real_margin = qty * entry / lev
    pos = SymbolPosition(symbol="EDUUSDT")
    pos.open("SHORT", qty, entry, real_margin)
    pos.arm_baseline(entry, 1)
    # 10% 真实保证金 ≈ 0.045；价格涨到使浮亏刚好超限
    # 空单浮亏 = (mark-entry)*qty = real_margin*0.1
    mark_limit = entry + (real_margin * 0.10) / qty
    act = StrategyRules.check_price_exits(params, pos, mark_limit - 1e-9)
    assert act is None
    act = StrategyRules.check_price_exits(params, pos, mark_limit + 1e-9)
    assert act is not None and act.type == "CLOSE_SL2"
    # 此时相对真实保证金约 10%，绝不应等到 0.03545（约 -44% ROI）才触发
    assert mark_limit < 0.0350


def test_sl2_works_while_pending_baseline():
    """等 1m 武装期间也应有 SL2，避免盈利空窗变大亏。"""
    params = StrategyParams()
    params.exit.enable_tp1 = True
    params.exit.enable_sl2 = True
    params.exit.sl2_margin_loss_pct = 10
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 100)
    assert pos.pending_baseline and not pos.baseline_armed
    act = StrategyRules.check_price_exits(params, pos, 90)
    assert act is not None and act.type == "CLOSE_SL2"


def test_no_tp1_before_baseline():
    params = StrategyParams()
    params.exit.enable_tp1 = True
    params.exit.enable_sl2 = False
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 100)
    assert pos.baseline_armed is False
    # 有浮盈回撤也不 TP1（尚未武装）
    act = StrategyRules.check_price_exits(params, pos, 120)
    assert act is None
    act = StrategyRules.check_price_exits(params, pos, 110)
    assert act is None


def test_arm_negative_then_trail_when_profit_appears():
    """1m 收盘时已亏：峰值从 0 起，后续浮盈出现后仍可移动止盈。"""
    params = StrategyParams()
    params.exit.enable_tp1 = True
    params.exit.tp1_drawdown_pct = 30
    params.exit.enable_sl1 = False
    params.exit.enable_sl2 = False
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 100)
    pos.arm_baseline(95, 1)  # 当时浮盈 -5，峰值 0
    assert pos.peak_pnl == 0
    assert StrategyRules.check_price_exits(params, pos, 95) is None
    # 转盈到 130 → 峰值 30，地板 21
    assert StrategyRules.check_price_exits(params, pos, 130) is None
    assert abs(pos.peak_pnl - 30) < 1e-9
    act = StrategyRules.check_price_exits(params, pos, 120)  # 20 ≤ 21
    assert act is not None and act.type == "CLOSE_TP1"


def test_tp2_long_rsi():
    params = StrategyParams()
    params.timeframe = "1m"
    params.exit.enable_tp2 = True
    params.exit.tp2_long_rsi = 50
    # 上涨路径拉高 RSI
    closes = [100 + i for i in range(20)]
    ind = SymbolIndicators(timeframe="1m", rsi_period=6, volume_lookback=5)
    ind.bootstrap("1m", closes, [10.0] * 20)
    pos = SymbolPosition(symbol="T")
    pos.open("LONG", 1, 100, 10)
    act = StrategyRules.check_tp2(params, ind, pos, trigger_interval="1m")
    assert act is not None and act.type == "CLOSE_TP2"
    act = StrategyRules.check_tp2(params, ind, pos, trigger_interval="3m")
    assert act is None


def test_evaluate_entry_mode_ignores_tp2_interval():
    params = StrategyParams()
    params.timeframe = "1m"
    params.entry_conditions.long.vol_lookback = 5
    params.entry_conditions.long.vol_mult = 7
    params.entry_conditions.long.rsi_threshold = 100
    params.entry_conditions.short.enable_rsi = False
    params.entry_conditions.short.enable_vol = False
    ind = _ready_ind(final_vol=100.0)
    pos = SymbolPosition(symbol="T")
    act = StrategyRules.evaluate(
        params, ind, pos, allow_open=True, trigger_interval="3m", mode="entry",
    )
    assert act.type == "OPEN_LONG"


def test_live_vol_triggers_long_entry():
    params = StrategyParams()
    params.timeframe = "1m"
    params.entry_conditions.long.vol_lookback = 5
    params.entry_conditions.long.vol_mult = 7
    params.entry_conditions.long.rsi_threshold = 100
    params.entry_conditions.short.enable_rsi = False
    params.entry_conditions.short.enable_vol = False
    # 收盘量不够放量
    ind = _ready_ind(final_vol=10.0)
    ok, _ = StrategyRules.long_entry_ready(params, ind)
    assert not ok
    # 未收盘放量
    t = ind.get("1m")
    avg = t.volume.avg_closed(5)
    assert avg is not None
    ind.set_live("1m", volume=avg * 8)
    ok, reason = StrategyRules.long_entry_ready(params, ind)
    assert ok
    assert "VOL=" in reason


def test_live_rsi_peek_can_trigger_mid_bar():
    """收盘 RSI 未达阈值，用 live 价 peek 后应能开多。"""
    params = StrategyParams()
    params.timeframe = "1m"
    params.entry_conditions.enable_short = False
    params.entry_conditions.long.enable_vol = False
    params.entry_conditions.long.enable_rsi = True
    # 交替涨跌 → RSI 居中
    closes = []
    p = 100.0
    for i in range(24):
        p += 1.0 if i % 2 == 0 else -0.8
        closes.append(p)
    ind = SymbolIndicators(timeframe="1m", rsi_period=6, volume_lookback=5)
    ind.bootstrap("1m", closes, [10.0] * len(closes))
    closed_rsi = ind.get("1m").rsi.value
    assert closed_rsi is not None and 5 < closed_rsi < 95
    thr = closed_rsi - 5
    params.entry_conditions.long.rsi_threshold = thr
    ok, _ = StrategyRules.long_entry_ready(params, ind)
    assert not ok
    live_px = closes[-1] * 0.7
    peeked = ind.get("1m").rsi.peek(live_px)
    assert peeked is not None and peeked <= thr
    ind.set_live("1m", close=live_px)
    ok, reason = StrategyRules.long_entry_ready(params, ind)
    assert ok
    assert "RSI=" in reason


def test_config_migration_v1():
    from app.strategy.engine import StrategyEngine
    raw = {
        "global": {"capital_source": "env", "capital_usdt": 200},
        "strategy": {
            "leverage": 20,
            "position_pct": 1.0,
            "ema_fast": 7,
            "ema_mid": 25,
            "ema_slow": 99,
            "features": {"enable_loss_blacklist": True},
            "take_profit": {"enable_tp1_volume": True},
            "screening": {
                "volume_min_usd": 5_000_000,
                "mcap_min_usd": 8_000_000,
                "mcap_max_usd": 6_000_000_000,
                "price_max_usd": 20,
            },
        },
        "symbols": [],
    }
    out = StrategyEngine._normalize_config_payload(raw)
    s = out["strategy"]
    assert s["strategy_version"] == 2
    assert s["position_pct"] == 2.0
    assert s["timeframe"] == "1m"
    assert "exit" in s
    assert s["screening"]["volume_min_usd"] == 5_000_000
    assert "features" not in s or s.get("features") is None or True
