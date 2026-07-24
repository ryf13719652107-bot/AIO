"""RSI + Volume 指标测试。"""

from app.strategy.indicators import RSIState, SymbolIndicators, VolumeWindow


def test_volume_avg_prior_excludes_current():
    vw = VolumeWindow(lookback=3)
    for v in (10, 20, 30, 100):
        vw.update(v)
    # prior = 10,20,30 avg=20; latest=100
    assert vw.latest == 100
    assert vw.avg_prior(3) == 20
    assert abs(vw.ratio(3) - 5.0) < 1e-9


def test_volume_not_ready_until_lookback_plus_one():
    vw = VolumeWindow(lookback=30)
    for i in range(30):
        vw.update(1.0)
    assert vw.avg_prior(30) is None
    vw.update(10.0)
    assert vw.avg_prior(30) == 1.0
    assert abs(vw.ratio(30) - 10.0) < 1e-9


def test_rsi_initializes():
    rsi = RSIState(period=6)
    prices = [100, 101, 102, 101, 100, 99, 98, 97]
    for p in prices:
        rsi.update(p)
    assert rsi.initialized
    assert rsi.value is not None


def test_symbol_indicators_ready():
    ind = SymbolIndicators(timeframe="1m", rsi_period=6, volume_lookback=5)
    closes = [100 + i * 0.1 for i in range(20)]
    vols = [10.0] * 19 + [100.0]
    ind.bootstrap("1m", closes, vols)
    assert ind.ready("1m")
    snap = ind.snapshot()
    assert "1m" in snap
    assert snap["1m"]["rsi"] is not None
    assert snap["1m"]["volume_ratio"] is not None


def test_live_volume_vs_closed_avg():
    vw = VolumeWindow(lookback=3)
    for v in (10, 20, 30):
        vw.update(v)
    assert vw.avg_closed(3) == 20.0
    assert vw.avg_prior(3) is None  # 只有 3 根，prior 需要 4

    ind = SymbolIndicators(timeframe="1m", rsi_period=6, volume_lookback=3)
    closes = [100 - i for i in range(12)]
    vols = [10.0] * 12
    ind.bootstrap("1m", closes, vols)
    t = ind.get("1m")
    # 清掉最后一根语义：live 相对已收盘均量
    prior_closed = t.volume.avg_closed(3)
    assert prior_closed is not None
    ind.set_live("1m", volume=prior_closed * 8, close=90.0)
    assert t.live_volume == prior_closed * 8
    snap = t.snapshot()
    assert snap["live"] is True
    assert snap["volume_ratio"] is not None
    assert abs(snap["volume_ratio"] - 8.0) < 1e-6
