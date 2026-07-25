"""
增量式单周期技术指标：
- RSI(6) Wilder 平滑（仅完整 K 收盘更新）
- 成交量窗口（均量严格不含当前根）
- 未收盘 live 成交量（实时开仓用，不写入窗口）
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


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

    def peek(self, live_close: float) -> Optional[float]:
        """用未收盘价估算当前 RSI，不改写内部状态（供实时开仓）。"""
        if not self.initialized or self._prev_close is None:
            return self.value
        delta = live_close - self._prev_close
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
        avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


@dataclass
class VolumeWindow:
    """成交量窗口。avg_prior 严格不含最新一根。"""
    lookback: int = 30
    volumes: deque = field(default_factory=lambda: deque(maxlen=200))

    def __post_init__(self):
        # 至少保留 lookback+1 根以便算 prior
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
        """最近 N 根已收盘均量（窗口内全部视为已收盘时用）。"""
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
    """单周期 RSI + Volume。"""

    def __init__(self, timeframe: str, rsi_period: int = 6, volume_lookback: int = 30):
        self.timeframe = timeframe
        self.rsi = RSIState(period=rsi_period)
        self.volume = VolumeWindow(lookback=volume_lookback)
        self.last_close: Optional[float] = None
        self.last_open: Optional[float] = None
        self.bar_count: int = 0
        # 未收盘 K：仅用于实时开仓 VOL，不写入 volumes / 不推进 RSI
        self.live_volume: Optional[float] = None
        self.live_close: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self.rsi.initialized and len(self.volume.volumes) >= self.volume.lookback + 1

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
        rsi = self.rsi.value
        if self.live_close is not None and self.rsi.initialized:
            peeked = self.rsi.peek(self.live_close)
            if peeked is not None:
                rsi = peeked
        if self.live_volume is not None:
            vol_latest = self.live_volume
            vol_avg = self.volume.avg_closed()
            vol_ratio = (
                (vol_latest / vol_avg) if vol_avg and vol_avg > 0 else None
            )
        else:
            vol_avg = self.volume.avg_prior()
            vol_latest = self.volume.latest
            vol_ratio = self.volume.ratio()
        return {
            "timeframe": self.timeframe,
            "rsi": round(rsi, 4) if rsi is not None else None,
            "volume": round(vol_latest, 4) if vol_latest is not None else None,
            "volume_avg": round(vol_avg, 4) if vol_avg is not None else None,
            "volume_ratio": round(vol_ratio, 4) if vol_ratio is not None else None,
            "ready": self.ready,
            "last_close": self.last_close,
            "last_open": self.last_open,
            "bar_count": self.bar_count,
            "live": self.live_volume is not None or self.live_close is not None,
        }


class SymbolIndicators:
    """一个币种：仅维护策略所选交易周期的指标。"""

    def __init__(self, timeframe: str = "1m", rsi_period: int = 6, volume_lookback: int = 30):
        self.timeframe = timeframe
        self.rsi_period = rsi_period
        self.volume_lookback = volume_lookback
        self.tf: dict[str, TimeframeIndicators] = {
            timeframe: TimeframeIndicators(timeframe, rsi_period, volume_lookback),
        }

    def get(self, timeframe: Optional[str] = None) -> TimeframeIndicators:
        tf = timeframe or self.timeframe
        if tf not in self.tf:
            self.tf[tf] = TimeframeIndicators(tf, self.rsi_period, self.volume_lookback)
        return self.tf[tf]

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
        return self.get(timeframe or self.timeframe).ready

    def all_ready(self) -> bool:
        return all(t.ready for t in self.tf.values())

    def snapshot(self, latest_price: Optional[float] = None) -> dict:
        return {
            tf: ind.snapshot(latest_price if tf == self.timeframe else None)
            for tf, ind in self.tf.items()
        }
