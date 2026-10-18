"""
增量式多周期技术指标（新策略 v3）：
- EMA(7/25/99)：SMA 种子 + 标准 EMA 递推
- MACD(12,26,9)：DIF/DEA/MACD柱，柱历史保留最近 8 根（判定连续3根缩小）
- RSI(6) Wilder 平滑
- 成交量窗口（均量严格不含当前根）

所有指标仅在 K 线收盘时更新；策略判定全部使用已收盘数据。
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from app.schemas import TIMEFRAMES


@dataclass
class RSIState:
    """Wilder RSI。"""
    period: int = 6
    value: Optional[float] = None
    initialized: bool = False
    _avg_gain: float = 0.0
    _avg_loss: float = 0.0
    _prev_close: Optional[float] = None
    _bootstrap_gains: list = field(default_factory=list)
    _bootstrap_losses: list = field(default_factory=list)

    def update(self, close: float) -> Optional[float]:
        if self._prev_close is None:
            self._prev_close = close
            return None

        delta = close - self._prev_close
        self._prev_close = close
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)

        if not self.initialized:
            self._bootstrap_gains.append(gain)
            self._bootstrap_losses.append(loss)
            if len(self._bootstrap_gains) >= self.period:
                self._avg_gain = sum(self._bootstrap_gains[-self.period:]) / self.period
                self._avg_loss = sum(self._bootstrap_losses[-self.period:]) / self.period
                self.initialized = True
                self.value = self._calc_rsi()
            return self.value

        self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
        self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        self.value = self._calc_rsi()
        return self.value

    def _calc_rsi(self) -> float:
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


@dataclass
class EMAState:
    """标准 EMA：前 period 根 SMA 种子，之后 EMA 递推。"""
    period: int
    value: Optional[float] = None
    initialized: bool = False
    _seed: list = field(default_factory=list)

    def update(self, close: float) -> Optional[float]:
        if not self.initialized:
            self._seed.append(float(close))
            if len(self._seed) >= self.period:
                self.value = sum(self._seed[-self.period:]) / self.period
                self.initialized = True
                self._seed.clear()
            return self.value
        k = 2.0 / (self.period + 1.0)
        self.value = float(close) * k + self.value * (1.0 - k)
        return self.value


@dataclass
class MACDState:
    """MACD(12,26,9)：DIF=EMA12-EMA26；DEA=DIF 的 EMA9（首根 DIF 作种子）；
    MACD柱 = DIF - DEA。柱历史保留最近 8 根。"""
    fast: int = 12
    slow: int = 26
    signal: int = 9
    _ema_fast: EMAState = field(default_factory=lambda: EMAState(period=12))
    _ema_slow: EMAState = field(default_factory=lambda: EMAState(period=26))
    _signal_prev: Optional[float] = None
    dif: Optional[float] = None
    dea: Optional[float] = None
    hist: Optional[float] = None
    hist_history: deque = field(default_factory=lambda: deque(maxlen=8))

    def update(self, close: float) -> None:
        self._ema_fast.update(close)
        self._ema_slow.update(close)
        if not (self._ema_fast.initialized and self._ema_slow.initialized):
            return
        dif = self._ema_fast.value - self._ema_slow.value
        self.dif = dif
        if self._signal_prev is None:
            self._signal_prev = dif
        else:
            k = 2.0 / (self.signal + 1.0)
            self._signal_prev = dif * k + self._signal_prev * (1.0 - k)
        self.dea = self._signal_prev
        self.hist = dif - self.dea
        self.hist_history.append(self.hist)

    def hist_shrinking_below_zero(self, bars: int = 3) -> bool:
        """多头口径：末 bars 根柱 <0 且逐根增大（0轴以下连续收缩）。"""
        h = list(self.hist_history)
        if len(h) < bars + 1:
            return False
        window = h[-(bars + 1):]
        if any(v >= 0 for v in window[1:]):
            return False
        return window[3] > window[2] > window[1] > window[0]

    def hist_shrinking_above_zero(self, bars: int = 3) -> bool:
        """空头口径：末 bars 根柱 >0 且逐根减小。"""
        h = list(self.hist_history)
        if len(h) < bars + 1:
            return False
        window = h[-(bars + 1):]
        if any(v <= 0 for v in window[1:]):
            return False
        return window[3] < window[2] < window[1] < window[0]


@dataclass
class VolumeWindow:
    """成交量窗口。avg_prior 严格不含最新一根。"""
    lookback: int = 30
    volumes: deque = field(default_factory=lambda: deque(maxlen=200))

    def __post_init__(self):
        maxlen = max(200, self.lookback + 5)
        self.volumes = deque(maxlen=maxlen)

    def update(self, volume: float):
        self.volumes.append(float(volume))

    @property
    def latest(self) -> Optional[float]:
        return self.volumes[-1] if self.volumes else None

    def avg_prior(self, lookback: Optional[int] = None) -> Optional[float]:
        """前 N 根完整 K 的均量（不含当前最新根）。"""
        n = int(lookback if lookback is not None else self.lookback)
        if n <= 0:
            return None
        if len(self.volumes) < n + 1:
            return None
        prior = list(self.volumes)[-(n + 1):-1]
        if len(prior) < n:
            return None
        return sum(prior) / n

    def avg_closed(self, lookback: Optional[int] = None) -> Optional[float]:
        """最近 N 根已收盘均量。"""
        n = int(lookback if lookback is not None else self.lookback)
        if n <= 0 or len(self.volumes) < n:
            return None
        vals = list(self.volumes)[-n:]
        return sum(vals) / n

    @property
    def avg(self) -> Optional[float]:
        """兼容旧接口：等同 avg_prior(lookback)。"""
        return self.avg_prior(self.lookback)

    def ratio(self, lookback: Optional[int] = None) -> Optional[float]:
        avg = self.avg_prior(lookback)
        latest = self.latest
        if avg is None or latest is None or avg <= 0:
            return None
        return latest / avg


class TimeframeIndicators:
    """单周期指标：EMA7/25/99 + MACD + RSI(6) + 成交量窗口。"""

    def __init__(self, timeframe: str, rsi_period: int = 6, volume_lookback: int = 30):
        self.timeframe = timeframe
        self.rsi = RSIState(period=rsi_period)
        self.volume = VolumeWindow(lookback=volume_lookback)
        self.ema7 = EMAState(period=7)
        self.ema25 = EMAState(period=25)
        self.ema99 = EMAState(period=99)
        self.macd = MACDState()
        self.last_close: Optional[float] = None
        self.last_open: Optional[float] = None
        self.bar_count: int = 0
        # 未收盘 live 数据：仅展示用，不写入指标
        self.live_volume: Optional[float] = None
        self.live_close: Optional[float] = None

    @property
    def ready(self) -> bool:
        """策略判定就绪：EMA99/MACD/RSI/30根均量全部可用。"""
        return (
            self.ema7.initialized
            and self.ema25.initialized
            and self.ema99.initialized
            and self.rsi.initialized
            and len(self.macd.hist_history) >= 4
            and len(self.volume.volumes) >= 31
        )

    def set_live(self, volume: Optional[float] = None, close: Optional[float] = None):
        if volume is not None:
            self.live_volume = float(volume)
        if close is not None:
            self.live_close = float(close)

    def clear_live(self):
        self.live_volume = None
        self.live_close = None

    def update(self, close: float, volume: Optional[float] = None,
               open_price: Optional[float] = None):
        self.ema7.update(close)
        self.ema25.update(close)
        self.ema99.update(close)
        self.macd.update(close)
        self.rsi.update(close)
        self.last_close = close
        if open_price is not None:
            self.last_open = open_price
        if volume is not None:
            self.volume.update(volume)
        self.clear_live()
        self.bar_count += 1

    def bootstrap(self, closes: list[float], volumes: Optional[list[float]] = None,
                  opens: Optional[list[float]] = None):
        for i, c in enumerate(closes):
            vol = volumes[i] if volumes and i < len(volumes) else None
            op = opens[i] if opens and i < len(opens) else None
            self.update(c, vol, op)

    def snapshot(self, latest_price: Optional[float] = None) -> dict:
        macd = self.macd
        hist_list = list(macd.hist_history)
        return {
            "timeframe": self.timeframe,
            "ema7": round(self.ema7.value, 6) if self.ema7.value is not None else None,
            "ema25": round(self.ema25.value, 6) if self.ema25.value is not None else None,
            "ema99": round(self.ema99.value, 6) if self.ema99.value is not None else None,
            "dif": round(macd.dif, 6) if macd.dif is not None else None,
            "dea": round(macd.dea, 6) if macd.dea is not None else None,
            "hist": round(macd.hist, 6) if macd.hist is not None else None,
            "hist_history": [round(v, 6) for v in hist_list[-4:]],
            "rsi": round(self.rsi.value, 4) if self.rsi.value is not None else None,
            "volume": round(self.volume.latest, 4) if self.volume.latest is not None else None,
            "volume_avg": (
                round(avg, 4) if (avg := self.volume.avg_prior()) is not None else None
            ),
            "volume_ratio": self.volume.ratio(),
            "ready": self.ready,
            "last_close": self.last_close,
            "last_open": self.last_open,
            "bar_count": self.bar_count,
            "live": self.live_volume is not None or self.live_close is not None,
        }


class SymbolIndicators:
    """一个币种：维护所有策略周期（1m/5m/15m/30m/1h/2h）的指标。"""

    def __init__(self, symbol: str = "", timeframes: tuple = TIMEFRAMES,
                 rsi_period: int = 6, volume_lookback: int = 30):
        self.symbol = symbol
        self.timeframes = tuple(timeframes)
        self.timeframe = "5m"  # 信号周期（兼容旧字段）
        self.rsi_period = rsi_period
        self.volume_lookback = volume_lookback
        self.tf: dict[str, TimeframeIndicators] = {
            tf: TimeframeIndicators(tf, rsi_period, volume_lookback)
            for tf in self.timeframes
        }

    def get(self, timeframe: str) -> TimeframeIndicators:
        tf = self.tf.get(timeframe)
        if tf is None:
            tf = TimeframeIndicators(timeframe, self.rsi_period, self.volume_lookback)
            self.tf[timeframe] = tf
        return tf

    def update(self, timeframe: str, close: float, volume: Optional[float] = None,
               open_price: Optional[float] = None):
        self.get(timeframe).update(close, volume, open_price)

    def set_live(self, timeframe: str, volume: Optional[float] = None,
                 close: Optional[float] = None):
        self.get(timeframe).set_live(volume, close)

    def bootstrap_timeframe(self, timeframe: str, closes: list[float],
                            volumes: Optional[list[float]] = None,
                            opens: Optional[list[float]] = None):
        self.get(timeframe).bootstrap(closes, volumes, opens)

    def bootstrap(self, timeframe: str, closes: list[float],
                  volumes: Optional[list[float]] = None,
                  opens: Optional[list[float]] = None):
        self.bootstrap_timeframe(timeframe, closes, volumes, opens)

    def ready(self, timeframe: Optional[str] = None) -> bool:
        if timeframe:
            return self.get(timeframe).ready
        return all(t.ready for t in self.tf.values())

    def all_ready(self) -> bool:
        return all(t.ready for t in self.tf.values())

    def snapshot(self, latest_price: Optional[float] = None) -> dict:
        return {
            tf: ind.snapshot(latest_price if tf == "5m" else None)
            for tf, ind in self.tf.items()
        }
