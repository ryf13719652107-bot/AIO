"""
全市场 24h ticker WebSocket 缓存（选币用，避免 REST futures_ticker 触发限流）。
流：!ticker@arr
"""

import asyncio
import json
import logging
from typing import Optional

import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)


class BinanceTickerBook:
    """维护 symbol -> {lastPrice, quoteVolume}，供选币读取。"""

    def __init__(self, testnet: bool = False):
        settings = get_settings()
        if testnet:
            self._stream_base = "wss://stream.binancefuture.com/stream"
        else:
            self._stream_base = settings.BINANCE_WS_STREAM or "wss://fstream.binance.com/market/stream"
        self._tickers: dict[str, dict] = {}
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self.frame_count = 0

    @property
    def size(self) -> int:
        return len(self._tickers)

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._ready.clear()
        self._task = asyncio.create_task(self._run(), name="binance-ticker-book")

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def get_24h_tickers(self) -> list[dict]:
        return list(self._tickers.values())

    def get_usdt_symbols(self) -> list[str]:
        """从 ticker 推断 USDT 永续（排除交割合约 *_YYMMDD）。"""
        out = []
        for sym in self._tickers:
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            if "_" in base:
                continue
            out.append(sym)
        return out

    async def _run(self):
        url = f"{self._stream_base}?streams=!ticker@arr"
        backoff = 1.0
        while not self._stop.is_set():
            try:
                logger.info("连接 Binance WS [ticker]: %s", url)
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**23,
                    open_timeout=20,
                ) as ws:
                    logger.info("Binance WS [ticker] 握手成功")
                    backoff = 1.0
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        self.frame_count += 1
                        self._ingest(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WS [ticker] 断开/失败: %s, %.1fs 后重连", e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    def _ingest(self, raw: str):
        try:
            payload = json.loads(raw)
        except Exception:
            return
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            return
        updated = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            sym = item.get("s")
            if not sym:
                continue
            self._tickers[sym] = {
                "symbol": sym,
                "lastPrice": item.get("c") or item.get("p") or "0",
                "quoteVolume": item.get("q") or "0",
            }
            updated += 1
        if updated and not self._ready.is_set():
            self._ready.set()
            logger.info("TickerBook 就绪: %d 个合约", len(self._tickers))
