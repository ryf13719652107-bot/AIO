"""
REST 轮询底包 — 当 WS 不可用（如腾讯云切流）时的备用方案。
对外暴露与 BinanceFuturesWS 相同的接口：start/stop + 两个回调。

策略：
- K 线：每 (symbol, interval) 起一个 task，定时调 futures_klines limit=2，
        取倒数第二根（已收盘的最新一根）。用 open_time 去重，新值才触发回调。
- markPrice：每个 symbol 一个 task，每 5s 调 futures_mark_price。
"""

import asyncio
import logging
from typing import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

KlineCloseHandler = Callable[[str, str, dict], Awaitable[None]]
KlineOpenHandler = Callable[[str, str, dict], Awaitable[None]]
KlineUpdateHandler = Callable[[str, str, dict], Awaitable[None]]
MarkPriceHandler = Callable[[str, float], Awaitable[None]]

# 每个周期 K 线轮询间隔（秒）。比 K 线本身周期短，确保收盘后及时拾起。
_KLINE_POLL_INTERVAL = {
    "1m": 12,
    "3m": 20,
    "5m": 25,
    "15m": 45,
    "30m": 60,
    "1h": 90,
    "2h": 120,
    "1d": 300,
}


class BinanceFuturesPoller:
    def __init__(self, client, symbols: Iterable[str], intervals: Iterable[str],
                 on_kline_close: KlineCloseHandler, on_mark_price: MarkPriceHandler,
                 mark_poll_sec: float = 1.0,
                 on_kline_open: KlineOpenHandler | None = None,
                 on_kline_update: KlineUpdateHandler | None = None):
        self.client = client                # BinanceFuturesClient（已 init）
        self.symbols = list(symbols)
        self.intervals = list(intervals)
        self.on_kline_close = on_kline_close
        self.on_mark_price = on_mark_price
        self.on_kline_open = on_kline_open
        self.on_kline_update = on_kline_update
        self.mark_poll_sec = max(0.2, float(mark_poll_sec))

        self._last_kline_open: dict[tuple[str, str], int] = {}
        self._last_forming_open: dict[tuple[str, str], int] = {}
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._kline_count = 0
        self._mark_count = 0

    async def start(self):
        self._stop.clear()
        logger.info("启动 REST 轮询模式：%d 个币 × %d 个周期 + 全市场 markPrice",
                    len(self.symbols), len(self.intervals))
        for sym in self.symbols:
            for tf in self.intervals:
                self._tasks.append(asyncio.create_task(
                    self._poll_kline_loop(sym, tf),
                    name=f"poll-kline-{sym}-{tf}",
                ))
        self._tasks.append(asyncio.create_task(
            self._poll_all_marks_loop(),
            name="poll-mark-all",
        ))
        self._tasks.append(asyncio.create_task(self._heartbeat(), name="poll-heartbeat"))

    async def stop(self):
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("REST 轮询已停止")

    async def _poll_kline_loop(self, symbol: str, interval: str):
        poll_sec = _KLINE_POLL_INTERVAL.get(interval, 30)
        # 错开启动，避免一开始 4 币 × 4 周期 = 16 个请求同时发
        await asyncio.sleep((hash((symbol, interval)) & 7) * 0.5)
        while not self._stop.is_set():
            try:
                # 拉最近 30 根，倒数第 1 根是当前未收盘，前 29 根都是已收盘
                # 用 last_seen 自愈：网络抖动丢失的 K 线下次会一次性补齐
                klines = await self.client.get_klines(symbol, interval, limit=30)
                if klines and len(klines) >= 2:
                    key = (symbol, interval)
                    last_seen = self._last_kline_open.get(key, 0)
                    new_klines = []
                    for k in klines[:-1]:           # 排除最后一根（未收盘）
                        ot = int(k[0])
                        if ot > last_seen:
                            new_klines.append(k)
                    # 按 open_time 顺序逐根处理，保证 Wilder 增量正确
                    new_klines.sort(key=lambda k: int(k[0]))
                    for k in new_klines:
                        ot = int(k[0])
                        await self.on_kline_close(symbol, interval, {
                            "t": ot, "T": int(k[6]),
                            "i": interval, "s": symbol,
                            "o": k[1], "h": k[2], "l": k[3], "c": k[4],
                            "v": k[5], "x": True,
                        })
                        self._last_kline_open[key] = ot
                        self._kline_count += 1
                    # 当前未收盘 K
                    forming = klines[-1]
                    fot = int(forming[0])
                    prev_f = self._last_forming_open.get(key)
                    forming_k = {
                        "t": fot, "T": int(forming[6]),
                        "i": interval, "s": symbol,
                        "o": forming[1], "h": forming[2], "l": forming[3],
                        "c": forming[4], "v": forming[5], "x": False,
                    }
                    if self.on_kline_open and (prev_f is None or fot > prev_f):
                        self._last_forming_open[key] = fot
                        if prev_f is not None:
                            await self.on_kline_open(symbol, interval, forming_k)
                    elif fot and prev_f is None:
                        self._last_forming_open[key] = fot
                    if self.on_kline_update:
                        await self.on_kline_update(symbol, interval, forming_k)
                    if len(new_klines) > 1:
                        logger.info("%s %s 一次性补齐 %d 根 K 线（之前丢了）",
                                    symbol, interval, len(new_klines))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("poll kline %s %s 失败: %s", symbol, interval, e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll_sec)
            except asyncio.TimeoutError:
                pass

    async def _poll_all_marks_loop(self):
        while not self._stop.is_set():
            try:
                tickers = await self.client.get_24h_tickers()
                sym_set = {s.upper() for s in self.symbols}
                for t in tickers:
                    sym = t.get("symbol", "")
                    if sym not in sym_set:
                        continue
                    mark = float(t.get("lastPrice") or 0)
                    if mark:
                        await self.on_mark_price(sym, mark)
                        self._mark_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("poll all marks 失败: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.mark_poll_sec)
            except asyncio.TimeoutError:
                pass

    async def update_symbols(self, symbols: Iterable[str]):
        await self.restart(symbols)

    async def restart(self, symbols: Iterable[str]):
        """热更新币种列表：停止旧轮询并重建任务。"""
        was_running = bool(self._tasks) and not self._stop.is_set()
        if was_running:
            await self.stop()
        self.symbols = list(symbols)
        if was_running:
            await self.start()

    async def _heartbeat(self):
        """每分钟输出一次轮询计数，便于运维确认轮询活着。"""
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                logger.info("REST 轮询心跳: klines=%d marks=%d", self._kline_count, self._mark_count)
