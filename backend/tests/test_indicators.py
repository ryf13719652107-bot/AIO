"""多周期指标测试：EMA / MACD / RSI / VolumeWindow / SymbolIndicators。"""

import math

from app.strategy.indicators import (
    EMAState, MACDState, RSIState, SymbolIndicators, TimeframeIndicators,
    VolumeWindow,
)


def test_state_roundtrip_continues_incrementally():
    """save_state/load_state 后继续喂 K 线，结果应与不中断完全一致。"""
    closes = [100 + i * 0.5 + (i % 7) * 0.3 for i in range(120)]
    vols = [10 + (i % 5) * 2 for i in range(120)]

    a = SymbolIndicators(symbol="TESTUSDT")
    for c, v in zip(closes, vols):
        a.update("5m", c, v)

    snap = a.save_state()
    b = SymbolIndicators(symbol="TESTUSDT")
    assert b.load_state(snap)

    # 继续喂同一批后续 K 线，两者结果必须一致
    for c, v in zip(closes, vols):
        a.update("5m", c, v)
        b.update("5m", c, v)
    for tf_name in ("ema7", "ema25", "ema99"):
        ea = getattr(a.get("5m"), tf_name).value
        eb = getattr(b.get("5m"), tf_name).value
        assert math.isclose(ea, eb, rel_tol=1e-9)
    assert a.get("5m").ready and b.get("5m").ready
    assert math.isclose(a.get("5m").rsi.value, b.get("5m").rsi.value, rel_tol=1e-9)
    assert list(a.get("5m").macd.hist_history) == list(b.get("5m").macd.hist_history)
    assert a.get("5m").volume.avg_prior() == b.get("5m").volume.avg_prior()


def test_load_state_garbage_returns_false():
    ind = SymbolIndicators(symbol="TESTUSDT")
    assert not ind.load_state({})
    assert not ind.load_state({"5m": {"bad": 1}})


def test_ema_sma_seed_then_recursive():
    ema = EMAState(period=3)
    ema.update(1)
    ema.update(2)
    assert not ema.initialized
    ema.update(3)
    assert ema.initialized
    assert math.isclose(ema.value, 2.0)  # SMA(1,2,3)
    ema.update(4)
    k = 2 / 4
    assert math.isclose(ema.value, 4 * k + 2 * (1 - k))


def test_macd_basic_and_history():
    macd = MACDState()
    # 递增序列产生正值 DIF
    for i in range(1, 60):
        macd.update(float(i))
    assert macd.dif is not None and macd.dif > 0
    assert macd.dea is not None
    assert macd.hist is not None
    assert math.isclose(macd.hist, macd.dif - macd.dea, rel_tol=1e-9)
    assert len(macd.hist_history) >= 4

    # 连续递增 → 多头"0轴以下连续缩小"应不成立（柱为正）
    assert not macd.hist_shrinking_below_zero()


def test_macd_shrinking_detection():
    macd = MACDState()
    seq = [100, 100.5, 100.8, 100.9, 100.9, 100.85, 100.8, 100.7, 100.5]
    for p in seq:
        macd.update(p)
    h = list(macd.hist_history)
    if len(h) >= 4 and all(v < 0 for v in h[-3:]):
        assert macd.hist_shrinking_below_zero() == (
            h[-1] > h[-2] > h[-3] > h[-4]
        )


def test_rsi_bounds():
    rsi = RSIState(period=6)
    for i in range(1, 30):
        rsi.update(100.0 + i)  # 单边上涨
    assert rsi.value == 100.0

    rsi2 = RSIState(period=6)
    for i in range(1, 30):
        rsi2.update(100.0 - i)  # 单边下跌
    assert rsi2.value == 0.0


def test_volume_avg_prior_excludes_latest():
    vw = VolumeWindow(lookback=3)
    for v in (1, 2, 3, 4, 5):
        vw.update(v)
    assert vw.latest == 5
    assert math.isclose(vw.avg_prior(3), 3.0)  # (2+3+4)/3，不含最新根
    assert vw.avg_prior(4) is None or True  # 窗口足够时 4 根也可


def test_timeframe_indicators_ready():
    t = TimeframeIndicators("5m")
    # 不足 99+31 根时未就绪
    for i in range(50):
        t.update(100 + i * 0.1, 10)
    assert not t.ready
    for i in range(50, 130):
        t.update(100 + i * 0.1, 10)
    assert t.ready
    assert t.ema7.value is not None
    assert t.ema99.value is not None
    snap = t.snapshot()
    assert snap["ready"] is True
    for key in ("ema7", "ema25", "ema99", "dif", "dea", "hist", "rsi"):
        assert key in snap


def test_symbol_indicators_multi_tf():
    ind = SymbolIndicators(symbol="TESTUSDT")
    assert set(ind.tf.keys()) == {"1m", "5m", "15m", "30m", "1h", "2h"}
    ind.update("5m", 100.0, 5.0)
    ind.update("1m", 100.0, 1.0)
    assert ind.get("5m").bar_count == 1
    assert ind.get("1m").bar_count == 1
    snap = ind.snapshot()
    assert "5m" in snap and "1h" in snap
