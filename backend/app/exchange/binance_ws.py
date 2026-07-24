"""
币安永续合约 K 线 WebSocket（SUBSCRIBE 分批订阅）。
默认使用 fstream.binance.com/market 端点（国内部分网络下主站无推送）。
"""

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Iterable, Optional

import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)

KlineCloseHandler = Callable[[str, str, dict], Awaitable[None]]
KlineOpenHandler = Callable[[str, str, dict], Awaitable[None]]
KlineUpdateHandler = Callable[[str, str, dict], Awaitable[None]]
MarkPriceHandler = Callable[[str, float], Awaitable[None]]

MAX_STREAMS_PER_CONN = 80


class BinanceFuturesWS:
    # 回退默认值（settings 优先）
    PROD_WS = "wss://fstream.binance.com/market/ws"
    PROD_STREAM = "wss://fstream.binance.com/market/stream"
    TEST_WS = "wss://stream.binancefuture.com/ws"
    TEST_STREAM = "wss://stream.binancefuture.com/stream"

    def __init__(self, symbols: Iterable[str], intervals: Iterable[str],
                 on_kline_close: KlineCloseHandler, on_mark_price: MarkPriceHandler,
                 testnet: bool = False,
                 on_reconnect: Optional[Callable[[], Awaitable[None]]] = None,
                 on_kline_open: Optional[KlineOpenHandler] = None,
                 on_kline_update: Optional[KlineUpdateHandler] = None):
        self.symbols = [s.lower() for s in symbols]
        self.intervals = list(intervals)
        self.on_kline_close = on_kline_close
        self.on_mark_price = on_mark_price
        self.on_kline_open = on_kline_open
        self.on_kline_update = on_kline_update
        self.on_reconnect = on_reconnect
        self.testnet = testnet
        settings = get_settings()
        if testnet:
            self._ws_base = self.TEST_WS
            self._stream_base = self.TEST_STREAM
        else:
            self._ws_base = settings.BINANCE_WS_BASE or self.PROD_WS
            self._stream_base = settings.BINANCE_WS_STREAM or self.PROD_STREAM
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._symbol_set: set[str] = set()
        self.frame_count = 0
        self.kline_close_count = 0
        self._sub_id = 1
        self._reconnect_count = 0
        self._last_reconnect_cb_at = 0.0
        self._ever_connected: set[str] = set()
        # (symbol, interval) -> 已见过的未收盘 K open_time，用于检测新K开盘
        self._forming_open: dict[tuple[str, str], int] = {}

    def _kline_streams(self) -> list[str]:
        return [f"{s}@kline_{tf}" for s in self.symbols for tf in self.intervals]

    @staticmethod
    def _chunk(streams: list[str], size: int) -> list[list[str]]:
        return [streams[i:i + size] for i in range(0, len(streams), size)]

    async def start(self):
        self._stop.clear()
        self._symbol_set = {s.upper() for s in self.symbols}
        streams = self._kline_streams()
        chunks = self._chunk(streams, MAX_STREAMS_PER_CONN)
        logger.info(
            "WS 计划: endpoint=%s | %d 币 × %d 周期 = %d 流, %d 个 K 线连接 + mark",
            self._ws_base, len(self.symbols), len(self.intervals),
            len(streams), len(chunks),
        )
        for i, chunk in enumerate(chunks):
            self._tasks.append(asyncio.create_task(
                self._run_subscribe(chunk, name_prefix=f"kline-{i}", delay=i * 0.25),
                name=f"binance-ws-kline-{i}",
            ))
        self._tasks.append(asyncio.create_task(
            self._run_mark_arr(delay=0.15),
            name="binance-ws-mark",
        ))
        self._tasks.append(asyncio.create_task(self._heartbeat(), name="binance-ws-hb"))

    async def stop(self):
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # 主动 stop/换订阅不算断线重连，避免误触发 REST 回补
        self._ever_connected.clear()

    async def update_symbols(self, symbols: Iterable[str]):
        self.symbols = [s.lower() for s in symbols]
        if self._tasks:
            await self.stop()
            await self.start()

    async def _heartbeat(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                logger.info(
                    "Binance WS 心跳: frames=%d kline_closes=%d",
                    self.frame_count, self.kline_close_count,
                )

    def _next_id(self) -> int:
        self._sub_id += 1
        return self._sub_id

    def _schedule_reconnect_cb(self):
        if not self.on_reconnect:
            return
        now = time.time()
        if now - self._last_reconnect_cb_at < 15.0:
            return
        self._last_reconnect_cb_at = now
        self._reconnect_count += 1
        asyncio.create_task(self._fire_reconnect(), name="ws-reconnect-backfill")

    async def _fire_reconnect(self):
        try:
            logger.info("WS 重连完成，触发 K 线回补 (#%d)", self._reconnect_count)
            await self.on_reconnect()
        except Exception as e:
            logger.warning("WS 重连回补回调失败: %s", e)

    async def _run_subscribe(self, streams: list[str], name_prefix: str, delay: float = 0.0):
        if delay:
            await asyncio.sleep(delay)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                logger.info("连接 Binance WS [%s]: SUBSCRIBE %d streams → %s",
                            name_prefix, len(streams), self._ws_base)
                async with websockets.connect(
                    self._ws_base,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**22,
                    open_timeout=20,
                ) as ws:
                    for part in self._chunk(streams, 40):
                        req = {"method": "SUBSCRIBE", "params": part, "id": self._next_id()}
                        await ws.send(json.dumps(req))
                        await asyncio.sleep(0.05)
                    logger.info("Binance WS [%s] 握手+订阅完成", name_prefix)
                    backoff = 1.0
                    # 仅非首次连接（曾连上后断开再连）才触发回补，且防抖避免多连接重复打 REST
                    if name_prefix in self._ever_connected:
                        self._schedule_reconnect_cb()
                    else:
                        self._ever_connected.add(name_prefix)
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        self.frame_count += 1
                        await self._handle_message(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WS [%s] 断开/失败: %s, %.1fs 后重连", name_prefix, e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    async def _run_mark_arr(self, delay: float = 0.0):
        if delay:
            await asyncio.sleep(delay)
        url = f"{self._stream_base}?streams=!markPrice@arr@1s"
        backoff = 1.0
        while not self._stop.is_set():
            try:
                logger.info("连接 Binance WS [mark]: %s", url)
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**22,
                    open_timeout=20,
                ) as ws:
                    logger.info("Binance WS [mark] 握手成功")
                    backoff = 1.0
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        self.frame_count += 1
                        await self._handle_message(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WS [mark] 断开/失败: %s, %.1fs 后重连", e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    async def _handle_message(self, raw: str):
        try:
            payload = json.loads(raw)
        except Exception:
            return

        if isinstance(payload, dict) and "result" in payload and "id" in payload:
            return

        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if item.get("e") == "markPriceUpdate":
                    symbol = item.get("s")
                    mark = float(item.get("p", 0) or 0)
                    if symbol and mark and (not self._symbol_set or symbol in self._symbol_set):
                        await self.on_mark_price(symbol, mark)
            return

        if not isinstance(data, dict):
            return

        e = data.get("e")
        if e == "kline":
            k = data.get("k", {})
            symbol = data.get("s") or k.get("s")
            interval = k.get("i")
            if not symbol or not interval:
                return
            try:
                open_ms = int(k.get("t") or 0)
            except Exception:
                open_ms = 0
            key = (symbol.upper(), interval)
            closed = k.get("x") is True or str(k.get("x")).lower() == "true"
            # 新K开盘：未收盘且 open_time 变化（兼容旧回调）
            if not closed and open_ms and self.on_kline_open:
                prev = self._forming_open.get(key)
                if prev is None or open_ms > prev:
                    self._forming_open[key] = open_ms
                    if prev is not None:
                        k_open = dict(k)
                        try:
                            k_open["_event_ms"] = int(data.get("E") or k.get("T") or 0)
                        except Exception:
                            k_open["_event_ms"] = 0
                        await self.on_kline_open(symbol, interval, k_open)
            if closed:
                k2 = dict(k)
                try:
                    k2["_event_ms"] = int(data.get("E") or k.get("T") or 0)
                except Exception:
                    k2["_event_ms"] = 0
                self.kline_close_count += 1
                await self.on_kline_close(symbol, interval, k2)
            elif self.on_kline_update:
                # 未收盘更新：实时成交量/开仓
                k2 = dict(k)
                try:
                    k2["_event_ms"] = int(data.get("E") or k.get("T") or 0)
                except Exception:
                    k2["_event_ms"] = 0
                await self.on_kline_update(symbol, interval, k2)
        elif e == "markPriceUpdate":
            symbol = data.get("s")
            mark = float(data.get("p", 0) or 0)
            if symbol and mark and (not self._symbol_set or symbol in self._symbol_set):
                await self.on_mark_price(symbol, mark)
