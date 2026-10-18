"""策略规则测试：开仓/回踩加仓/趋势加仓/TP1-6 止盈。"""

from collections import deque

from app.schemas import StrategyParams
from app.strategy.indicators import SymbolIndicators
from app.strategy.position import SymbolPosition
from app.strategy.signal import StrategyRules


def _set_tf(ind, tf, *, e7=None, e25=None, e99=None, rsi=None, hist=None,
            dif=None, dea=None, volumes=None):
    t = ind.get(tf)
    for attr, v in (("ema7", e7), ("ema25", e25), ("ema99", e99)):
        if v is not None:
            st = getattr(t, attr)
            st.value = v
            st.initialized = True
    if rsi is not None:
        t.rsi.value = rsi
        t.rsi.initialized = True
    if hist is not None:
        t.macd.hist_history = deque(hist, maxlen=8)
    if dif is not None:
        t.macd.dif = dif
    if dea is not None:
        t.macd.dea = dea
    if volumes is not None:
        t.volume.volumes.clear()
        t.volume.volumes.extend(volumes)
    return t


def _bull_ind() -> SymbolIndicators:
    """满足全部多头开仓条件的指标。"""
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", e7=105, e25=100, e99=95, rsi=50,
            hist=[-4.0, -3.0, -2.0, -1.0], dif=1.0, dea=0.5,
            volumes=[1.0] * 30 + [100.0])
    # 引擎 K 线键为 1h/2h（即 60 分钟/120 分钟周期）
    for tf in ("15m", "30m", "1h", "2h"):
        _set_tf(ind, tf, e7=110, e25=105, e99=100)
    return ind


def _bear_ind() -> SymbolIndicators:
    """满足全部空头开仓条件的指标（镜像）。"""
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", e7=95, e25=100, e99=105, rsi=60,
            hist=[4.0, 3.0, 2.0, 1.0], dif=-1.0, dea=-0.5,
            volumes=[1.0] * 30 + [100.0])
    for tf in ("15m", "30m", "1h", "2h"):
        _set_tf(ind, tf, e7=90, e25=95, e99=100)
    return ind


def _long_pos(entry: float = 100.0) -> SymbolPosition:
    pos = SymbolPosition(symbol="TESTUSDT")
    pos.open("LONG", 1.0, entry, 10.0)
    return pos


def _short_pos(entry: float = 100.0) -> SymbolPosition:
    pos = SymbolPosition(symbol="TESTUSDT")
    pos.open("SHORT", 1.0, entry, 10.0)
    return pos


# ---------- 开仓 ----------
def test_entry_long_all_conditions_met():
    params = StrategyParams()
    ok, reason = StrategyRules.entry_ready(params, _bull_ind(), "LONG")
    assert ok
    assert "EMA" in reason


def test_entry_short_all_conditions_met():
    params = StrategyParams()
    ok, reason = StrategyRules.entry_ready(params, _bear_ind(), "SHORT")
    assert ok


def test_entry_long_blocked_by_rsi():
    params = StrategyParams()
    ind = _bull_ind()
    ind.get("5m").rsi.value = 80  # ≥65 不满足
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG")
    assert not ok


def test_entry_single_condition_can_be_disabled():
    params = StrategyParams()
    params.entry.long.ema_5m = False      # 关掉 EMA7>25
    params.entry.long.ema_5m_99 = False   # 关掉 均>99
    ind = _bull_ind()
    e7 = ind.get("5m").ema7
    e7.value = 90  # 故意让 EMA7<EMA25
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG")
    assert ok


def test_entry_all_toggles_off_disables_side():
    params = StrategyParams()
    cfg = params.entry.long
    for f in ("ema_5m", "ema_5m_99", "rsi", "macd_dif", "macd_hist",
              "ema_15m", "ema_30m", "ema_60m", "ema_120m"):
        setattr(cfg, f, False)
    ok, _ = StrategyRules.entry_ready(params, _bull_ind(), "LONG")
    assert not ok


def test_entry_macd_dif_and_hist_independent():
    """MACD 两条件独立：只勾其一，另一条件不满足也可开仓。"""
    # 只勾 DIF>DEA，柱不缩小 → 仍可开
    params = StrategyParams()
    params.entry.long.macd_hist = False
    ind = _bull_ind()
    _set_tf(ind, "5m", hist=[-1.0, -3.0, -2.0, -4.0])  # 柱非连续缩小
    ok, reason = StrategyRules.entry_ready(params, ind, "LONG")
    assert ok and "DIF" in reason

    # 只勾柱缩小，DIF<DEA → 仍可开
    params2 = StrategyParams()
    params2.entry.long.macd_dif = False
    ind2 = _bull_ind()
    _set_tf(ind2, "5m", dif=-1.0, dea=0.5)  # DIF<DEA 不满足
    ok2, reason2 = StrategyRules.entry_ready(params2, ind2, "LONG")
    assert ok2 and "柱" in reason2


def test_entry_direction_switch():
    params = StrategyParams()
    params.entry.enable_long = False
    ok, _ = StrategyRules.entry_ready(params, _bull_ind(), "LONG")
    assert not ok


def test_entry_high_tf_requires_ema7_above_ema25():
    """高周期开仓条件：EMA7 与 EMA25 均>EMA99 但 EMA7<EMA25 → 不满足。"""
    params = StrategyParams()
    ind = _bull_ind()
    # 1h：7&25 均>99 但 7<25
    _set_tf(ind, "1h", e7=101, e25=105, e99=100)
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG")
    assert not ok


# ---------- 回踩加仓 ----------
def test_pullback_long_requires_armed_then_dip():
    params = StrategyParams()
    ind = _bull_ind()
    _set_tf(ind, "5m", e7=100)
    pos = _long_pos()

    # mark 在 EMA7 上方 → 武装，不触发
    act = StrategyRules.check_pullback(params, ind, pos, 101)
    assert act is None
    assert pos.get_pullback_armed("ema7")

    # 回踩 ≤ EMA7 → 触发
    act = StrategyRules.check_pullback(params, ind, pos, 99.5)
    assert act is not None and act.type == "ADD" and act.trigger_key == "ema7"

    # 模拟加仓后该档仅一次；mark 继续下探时 ema7 不再触发（ema25 武装在先属正常级联）
    pos.add(1.0, 99.5, 10.0, "ema7")
    pos.set_pullback_armed("ema7", True)
    act2 = StrategyRules.check_pullback(params, ind, pos, 99.0)
    assert act2 is None or act2.trigger_key != "ema7"


def test_pullback_long_dip_without_armed_no_fire():
    params = StrategyParams()
    ind = _bull_ind()
    _set_tf(ind, "5m", e7=100)
    pos = _long_pos()
    # 从未站上 EMA 直接回踩 → 不触发
    assert StrategyRules.check_pullback(params, ind, pos, 99.0) is None
    assert not pos.get_pullback_armed("ema7")


def test_pullback_short_mirror():
    params = StrategyParams()
    ind = _bear_ind()
    _set_tf(ind, "5m", e7=100)
    pos = _short_pos()

    assert StrategyRules.check_pullback(params, ind, pos, 99.0) is None
    assert pos.get_pullback_armed("ema7")
    act = StrategyRules.check_pullback(params, ind, pos, 100.5)
    assert act is not None and act.trigger_key == "ema7"


# ---------- 趋势加仓 ----------
def test_trend_add_15m_once():
    params = StrategyParams()
    ind = _bull_ind()
    pos = _long_pos()
    act = StrategyRules.trend_add_ready(params, ind, pos, "15m")
    assert act is not None and act.trigger_key == "t15m"
    pos.add(1.0, 100.0, 10.0, "t15m")
    assert StrategyRules.trend_add_ready(params, ind, pos, "15m") is None


def test_trend_add_30m_short():
    params = StrategyParams()
    ind = _bear_ind()
    pos = _short_pos()
    act = StrategyRules.trend_add_ready(params, ind, pos, "30m")
    assert act is not None and act.trigger_key == "t30m"


def test_trend_add_requires_both_above_99():
    params = StrategyParams()
    ind = _bull_ind()
    _set_tf(ind, "15m", e25=95)  # EMA25 < EMA99
    pos = _long_pos()
    assert StrategyRules.trend_add_ready(params, ind, pos, "15m") is None


# ---------- 止盈 ----------
def test_tp1_on_1m_close_only():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "1m", e25=90, e99=95)
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "1m")
    assert act is not None and act.type == "CLOSE_TP1"
    # 非 1m 收盘不触发
    assert StrategyRules.check_exit(params, ind, pos, "5m") is None


def test_tp1_short_mirror():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "1m", e25=96, e99=95)
    pos = _short_pos()
    act = StrategyRules.check_exit(params, ind, pos, "1m")
    assert act is not None and act.type == "CLOSE_TP1"


def test_tp2_tp3_on_5m_close():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", e7=90, e25=95, e99=85,
            volumes=[1.0] * 31)
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "5m")
    # TP2 优先（EMA7<EMA25）
    assert act is not None and act.type == "CLOSE_TP2"


def test_tp4_on_15m_close():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "15m", e7=90, e25=95)
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "15m")
    assert act is not None and act.type == "CLOSE_TP4"


def test_tp5_volume_surge():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", volumes=[1.0] * 30 + [100.0])
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "5m")
    assert act is not None and act.type == "CLOSE_TP5"


def test_tp6_long_5m_level():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", rsi=93.0, volumes=[1.0] * 31)
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "5m")
    assert act is not None and act.type == "CLOSE_TP6"
    assert act.trigger_key == "t5m"
    assert act.close_pct == 10.0


def test_tp6_long_higher_tf_levels():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "15m", rsi=95.0)
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "15m")
    assert act is not None and act.trigger_key == "t15m" and act.close_pct == 20.0

    # 60m(1h) & 120m(2h) 双条件，2h 收盘判定
    ind2 = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind2, "1h", rsi=96.5)
    _set_tf(ind2, "2h", rsi=96.1)
    act = StrategyRules.check_exit(params, ind2, pos, "2h")
    assert act is not None and act.trigger_key == "t60m_120m" and act.close_pct == 20.0

    # 只有 60m(1h) 满足不够（1h RSI=96 未到 t60m 档 97，2h 不满足）
    ind3 = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind3, "1h", rsi=96.0)
    _set_tf(ind3, "2h", rsi=50.0)
    assert StrategyRules.check_exit(params, ind3, pos, "1h") is None


def test_tp6_short_mirror_pcts():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", rsi=9.0, volumes=[1.0] * 31)
    pos = _short_pos()
    act = StrategyRules.check_exit(params, ind, pos, "5m")
    assert act is not None and act.trigger_key == "t5m" and act.close_pct == 10.0

    ind2 = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind2, "15m", rsi=6.0)
    act = StrategyRules.check_exit(params, ind2, pos, "15m")
    assert act is not None and act.trigger_key == "t15m" and act.close_pct == 30.0


def test_tp6_fire_once_per_level():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", rsi=95.0, volumes=[1.0] * 31)
    pos = _long_pos()
    pos.fire_tp6("t5m")  # 引擎在执行后标记
    act = StrategyRules.check_exit(params, ind, pos, "5m")
    # t5m 已触发不再返回；其余 5m 条件（TP2/3/5）也未满足 → None
    assert act is None


def test_full_close_priority_over_tp6():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", e7=90, e25=95, e99=85, rsi=95.0,
            volumes=[1.0] * 31)
    pos = _long_pos()
    act = StrategyRules.check_exit(params, ind, pos, "5m")
    # 同 bar 全平（TP2）优先于 TP6
    assert act is not None and act.type == "CLOSE_TP2"


def test_exit_disabled_tp():
    params = StrategyParams()
    params.exit.enable_tp2 = False
    params.exit.enable_tp3 = False
    params.exit.enable_tp5 = False
    ind = SymbolIndicators(symbol="TESTUSDT")
    _set_tf(ind, "5m", e7=90, e25=95, e99=85, volumes=[1.0] * 31)
    pos = _long_pos()
    assert StrategyRules.check_exit(params, ind, pos, "5m") is None
