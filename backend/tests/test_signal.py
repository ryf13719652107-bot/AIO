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
    e7.value = 97  # 故意让 EMA7<EMA25（但仍>EMA99，不触发新条件）
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG")
    assert ok


def test_entry_all_toggles_off_disables_side():
    params = StrategyParams()
    cfg = params.entry.long
    for f in ("ema_5m", "ema_5m_99", "ema_5m_7_99", "rsi", "macd_dif", "macd_hist",
              "ema_15m_cross", "ema_15m_over99", "ema_30m_cross", "ema_30m_over99",
              "ema_60m_cross", "ema_60m_over99", "ema_120m_cross", "ema_120m_over99"):
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
    """高周期条件A独立：1h EMA7<EMA25（即使均>99）→ 开仓不满足。"""
    params = StrategyParams()
    ind = _bull_ind()
    _set_tf(ind, "1h", e7=101, e25=105, e99=100)
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG")
    assert not ok


def test_entry_high_tf_cross_and_over99_independent():
    """高周期两个开关独立：只勾 7>25（关均>99），1h 均<99 也可开。"""
    params = StrategyParams()
    params.entry.long.ema_60m_over99 = False  # 只勾 60m 7>25
    ind = _bull_ind()
    _set_tf(ind, "1h", e7=110, e25=105, e99=120)  # 7>25 ✓ 但均>99 ✗
    ok, reason = StrategyRules.entry_ready(params, ind, "LONG")
    assert ok and "1h" in reason

    # 只勾 均>99（关 7>25），60m 7<25 也可开
    params2 = StrategyParams()
    params2.entry.long.ema_30m_cross = False
    ind2 = _bull_ind()
    _set_tf(ind2, "30m", e7=101, e25=105, e99=100)  # 7<25 ✗ 均>99 ✓
    ok2, reason2 = StrategyRules.entry_ready(params2, ind2, "LONG")
    assert ok2 and "30m" in reason2


# ---------- 开仓 5m EMA7 vs EMA99 状态条件（独立开关） ----------
def test_entry_ema5_7_99_blocks_when_unmet():
    """开关开启：EMA7 未站上/跌破 EMA99（状态）→ 不开。"""
    params = StrategyParams()
    ind = _bull_ind()
    _set_tf(ind, "5m", e7=94, e25=95, e99=100)  # EMA7<EMA99，多头不满足
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG", base_armed=(True, True, True))
    assert not ok

    ind2 = _bear_ind()
    _set_tf(ind2, "5m", e7=106, e25=105, e99=100)  # EMA7>EMA99，空头不满足
    ok2, _ = StrategyRules.entry_ready(params, ind2, "SHORT", base_armed=(True, True, True))
    assert not ok2


def test_entry_ema5_7_99_reason_includes_label():
    """开关开启且满足 → 开仓理由包含 EMA7>EMA99。"""
    params = StrategyParams()
    ok, reason = StrategyRules.entry_ready(params, _bull_ind(), "LONG",
                                           base_armed=(True, True, True))
    assert ok and "EMA7>EMA99" in reason
    ok2, reason2 = StrategyRules.entry_ready(params, _bear_ind(), "SHORT",
                                             base_armed=(True, True, True))
    assert ok2 and "EMA7<EMA99" in reason2


def test_entry_ema5_7_99_toggle_off_ignores_state():
    """开关关闭：EMA7 处于 EMA99 错误侧也不拦截，其余条件满足即开。"""
    params = StrategyParams()
    params.entry.long.ema_5m_7_99 = False
    ind = _bull_ind()
    _set_tf(ind, "5m", e7=94, e25=95, e99=100)  # EMA7<EMA99 但开关已关
    ok, _ = StrategyRules.entry_ready(params, ind, "LONG", base_armed=(True, True, True))
    assert ok

    params2 = StrategyParams()
    params2.entry.short.ema_5m_7_99 = False
    ind2 = _bear_ind()
    _set_tf(ind2, "5m", e7=106, e25=105, e99=100)  # EMA7>EMA99 但开关已关
    ok2, _ = StrategyRules.entry_ready(params2, ind2, "SHORT", base_armed=(True, True, True))
    assert ok2


def test_entry_ema5_7_99_independent_from_cross():
    """状态条件与穿越基础条件独立：无穿越标志但状态满足时，该条件自身不拦截。"""
    params = StrategyParams()
    params.entry.long.ema_5m = False
    params.entry.long.ema_5m_99 = False
    ok, reason = StrategyRules.entry_ready(params, _bull_ind(), "LONG",
                                           base_armed=(False, False, False))
    assert ok and "EMA7>EMA99" in reason


# ---------- 开仓基础条件（5m EMA 上穿/下穿事件） ----------
def test_entry_base_cross_required_not_state():
    """基础条件是穿越事件：状态延续（EMA7 早已>25）但穿越未重新出现 → 不开。"""
    params = StrategyParams()
    ok, _ = StrategyRules.entry_ready(params, _bull_ind(), "LONG",
                                      base_armed=(False, False, False))
    assert not ok
    ok2, _ = StrategyRules.entry_ready(params, _bear_ind(), "SHORT",
                                       base_armed=(False, False, False))
    assert not ok2


def test_entry_base_cross_all_triggered():
    """三对穿越标志全部触发（基础条件重新检测过）→ 可开，理由含上穿/下穿。"""
    params = StrategyParams()
    ok, reason = StrategyRules.entry_ready(params, _bull_ind(), "LONG",
                                           base_armed=(True, True, True))
    assert ok and "上穿" in reason
    ok2, reason2 = StrategyRules.entry_ready(params, _bear_ind(), "SHORT",
                                              base_armed=(True, True, True))
    assert ok2 and "下穿" in reason2


def test_entry_base_cross_partial_not_enough():
    """7×25 已上穿但 7/25×99 未触发 → 基础条件不全不开；关 99 条件后仅 7×25 即可。"""
    params = StrategyParams()
    ok, _ = StrategyRules.entry_ready(params, _bull_ind(), "LONG",
                                      base_armed=(True, False, False))
    assert not ok
    params.entry.long.ema_5m_99 = False
    ok2, _ = StrategyRules.entry_ready(params, _bull_ind(), "LONG",
                                       base_armed=(True, False, False))
    assert ok2


# ---------- 引擎 5m EMA 上穿/下穿状态机 ----------
def _cross_engine():
    """跳过 __init__ 的引擎实例：_update_ema5_cross 只依赖 _ema5_cross。"""
    from app.strategy.engine import StrategyEngine
    eng = StrategyEngine.__new__(StrategyEngine)
    eng._ema5_cross = {}
    return eng


def test_engine_ema5_cross_startup_and_fresh_cross():
    """启动前已出现的穿越不算：首次收盘只记基准；重新下穿/上穿才触发标志。"""
    eng = _cross_engine()
    ind = SymbolIndicators(symbol="TESTUSDT")
    t5 = ind.get("5m")
    _set_tf(ind, "5m", e7=105, e25=100, e99=95)      # 启动前已是多头排列
    up, down = eng._update_ema5_cross("TESTUSDT", t5)
    assert up == (False, False, False) and down == (False, False, False)
    up, down = eng._update_ema5_cross("TESTUSDT", t5)  # 状态延续仍不算
    assert up == (False, False, False) and down == (False, False, False)
    # 启动后重新下穿：EMA7 跌破 EMA25 与 EMA99（EMA25 仍在 99 上方）
    _set_tf(ind, "5m", e7=98, e25=100, e99=99)
    up, down = eng._update_ema5_cross("TESTUSDT", t5)
    assert down == (True, True, False) and up == (False, False, False)
    # 再上穿：反向穿越清 down 置 up
    _set_tf(ind, "5m", e7=105, e25=100, e99=99)
    up, down = eng._update_ema5_cross("TESTUSDT", t5)
    assert up == (True, True, False) and down == (False, False, False)


def test_engine_ema5_cross_pair_25_99_independent():
    """三对独立跟踪：EMA25×99 下穿触发 down、重新上穿补齐 up 三对。"""
    eng = _cross_engine()
    ind = SymbolIndicators(symbol="TESTUSDT")
    t5 = ind.get("5m")
    _set_tf(ind, "5m", e7=90, e25=100, e99=95)       # 基准 (F,F,T)
    eng._update_ema5_cross("TESTUSDT", t5)
    _set_tf(ind, "5m", e7=105, e25=100, e99=95)      # 7 上穿 25/99
    up, down = eng._update_ema5_cross("TESTUSDT", t5)
    assert up == (True, True, False) and down == (False, False, False)
    _set_tf(ind, "5m", e7=105, e25=94, e99=95)       # EMA25 下穿 99
    up, down = eng._update_ema5_cross("TESTUSDT", t5)
    assert up == (True, True, False) and down == (False, False, True)
    _set_tf(ind, "5m", e7=105, e25=100, e99=95)      # EMA25 重新上穿 99
    up, down = eng._update_ema5_cross("TESTUSDT", t5)
    assert up == (True, True, True) and down == (False, False, False)


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


# ---------- TP6 实时盯价 ----------
def _live_rsi_ready(ind, tf, avg_gain=1.0, avg_loss=1.0, last_close=100.0):
    """构造可试算实时 RSI 的内部态（_live_view 需要 _avg_gain/_avg_loss/last_close）。"""
    t = _set_tf(ind, tf, rsi=50.0, volumes=[1.0] * 31)
    t.last_close = last_close
    t.rsi._avg_gain = avg_gain
    t.rsi._avg_loss = avg_loss
    return t


def test_tp6_live_check_long_triggers_before_close():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _live_rsi_ready(ind, "5m")
    pos = _long_pos()
    # 收盘 RSI 仅 50（不触发）；mark=200 拉高实时试算 RSI≈95.4 ≥93 → 触发
    act = StrategyRules.tp6_live_check(params, ind, pos, mark=200.0)
    assert act is not None and act.type == "CLOSE_TP6"
    assert act.trigger_key == "t5m" and act.close_pct == 10.0
    # mark 无变动（=last_close）时实时 RSI=50 → 不触发
    assert StrategyRules.tp6_live_check(params, ind, pos, mark=100.0) is None


def test_tp6_live_check_short_direction_and_armed_guard():
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _live_rsi_ready(ind, "5m")
    pos = _short_pos()
    # 空单：mark 上涨 RSI 升高 → 不满足 ≤10
    assert StrategyRules.tp6_live_check(params, ind, pos, mark=200.0) is None
    # 空单：mark=50 → delta=-50 → 实时 RSI≈8.3 ≤10 → 触发
    act = StrategyRules.tp6_live_check(params, ind, pos, mark=50.0)
    assert act is not None and act.trigger_key == "t5m" and act.close_pct == 10.0


def test_tp6_live_check_remaining_levels_after_breakeven_armed():
    """保本损武装后：已触发档不再重复；剩余档位仍可先到先触发（文档L59）。"""
    params = StrategyParams()
    ind = SymbolIndicators(symbol="TESTUSDT")
    _live_rsi_ready(ind, "5m")
    _live_rsi_ready(ind, "15m")
    pos = _short_pos()
    pos.fire_tp6("t5m")     # t5m 已触发（保本损同时武装）
    pos.breakeven_armed = True
    # mark=50：t5m 已 fired 跳过，但 15m 档实时 RSI≈8.3 ≤7?否→不触发 t15m（阈值7）
    assert StrategyRules.tp6_live_check(params, ind, pos, mark=50.0) is None
    # mark 更低（如 30）→ 15m 实时 RSI≈6.25 ≤7 → 武装后仍触发 t15m 平30%
    act = StrategyRules.tp6_live_check(params, ind, pos, mark=30.0)
    assert act is not None and act.trigger_key == "t15m" and act.close_pct == 30.0


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


# ---------- 风控强平（硬止损 / 时间强平） ----------
def test_risk_stop_hard_stop():
    from datetime import datetime, timedelta
    exit_cfg = StrategyParams().exit
    pos = _long_pos(entry=100.0)  # qty=1, margin=10
    # 浮亏率 = (98-100)*1/10 = -20% → 触发硬止损
    hit = StrategyRules.risk_stop(exit_cfg, pos, mark=98.0)
    assert hit is not None and hit[0] == "CLOSE_HARD_STOP"
    # 浮亏率 -10% 不触发
    assert StrategyRules.risk_stop(exit_cfg, pos, mark=99.0) is None
    # 关闭硬止损后不触发
    exit_cfg.enable_hard_stop = False
    assert StrategyRules.risk_stop(exit_cfg, pos, mark=98.0) is None
    exit_cfg.enable_hard_stop = True
    # 空仓不触发
    flat = SymbolPosition(symbol="TESTUSDT")
    assert StrategyRules.risk_stop(exit_cfg, flat, mark=98.0) is None


def test_risk_stop_time_stop():
    from datetime import datetime, timedelta
    exit_cfg = StrategyParams().exit
    pos = _long_pos()
    now = datetime.utcnow()
    # 持仓 1 小时：不触发
    assert StrategyRules.risk_stop(
        exit_cfg, pos, mark=100.0, now=now + timedelta(hours=1)) is None
    # 持仓 ≥12 小时 → 触发时间强平
    hit = StrategyRules.risk_stop(
        exit_cfg, pos, mark=100.0, now=now + timedelta(hours=12.5))
    assert hit is not None and hit[0] == "CLOSE_TIME_STOP"
    # 关闭时间强平后不触发
    exit_cfg.enable_time_stop = False
    assert StrategyRules.risk_stop(
        exit_cfg, pos, mark=100.0, now=now + timedelta(hours=12.5)) is None


# ---------- 选币 120 分钟 RSI(6) 筛选 ----------
def test_screener_rsi_2h_filter():
    import asyncio
    from app.strategy.screener import SymbolScreener

    class FakeExchange:
        async def get_24h_tickers(self):
            return [
                {"symbol": "AAAUSDT", "lastPrice": "1", "quoteVolume": "300"},
                {"symbol": "BBBUSDT", "lastPrice": "1", "quoteVolume": "200"},
                {"symbol": "CCCUSDT", "lastPrice": "1", "quoteVolume": "100"},
            ]

        async def get_usdt_perpetual_symbols(self):
            return ["AAAUSDT", "BBBUSDT", "CCCUSDT"]

        async def get_klines(self, symbol, interval, limit=200):
            assert interval == "2h"
            if symbol == "AAAUSDT":     # 连涨 → RSI=100
                closes = [100 + i for i in range(31)]
            elif symbol == "BBBUSDT":   # 连跌 → RSI=0
                closes = [200 - i for i in range(31)]
            else:                       # 涨跌交替 → RSI≈50
                closes = [100 + (i % 2) for i in range(31)]
            return [[i * 100, "1", "1", "1", str(c), "1", (i + 1) * 100]
                    for i, c in enumerate(closes)]

    params = StrategyParams()
    params.screening.enable_mcap = False
    params.screening.enable_volume = False
    params.screening.enable_price = False
    screener = SymbolScreener()

    # 两个区间都开（OR）：连涨/连跌保留，中位 RSI 剔除
    results = asyncio.run(screener.screen(FakeExchange(), params))
    assert {r.symbol for r in results} == {"AAAUSDT", "BBBUSDT"}

    # 只开 ≥上限：连跌 BBB 被剔除
    params.screening.enable_rsi_2h_low = False
    results2 = asyncio.run(screener.screen(FakeExchange(), params))
    assert {r.symbol for r in results2} == {"AAAUSDT"}

    # 两个 RSI 筛选都关：全部保留
    params.screening.enable_rsi_2h_high = False
    results3 = asyncio.run(screener.screen(FakeExchange(), params))
    assert {r.symbol for r in results3} == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}
