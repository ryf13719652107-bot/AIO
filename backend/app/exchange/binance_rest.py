"""
币安永续合约 REST 客户端封装：
- 历史 K 线（用于指标 bootstrap）
- 杠杆 / 保证金模式（默认全仓 CROSSED）
- 市价开/平仓（带 clientOrderId 幂等）
- 账户余额 / 持仓查询
- exchangeInfo（精度）

支持 PAPER_TRADING 模式：模拟成交（按当前 markPrice），不触达真实账户。
"""

import asyncio
import logging
import time
import uuid
from typing import Optional

from binance import AsyncClient
from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)


class BinanceFuturesClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.paper = paper
        self._client: Optional[AsyncClient] = None
        self._symbol_filters: dict[str, dict] = {}
        self._usdt_perpetuals: list[str] = []
        # 选币优先读 WS TickerBook，避免 REST futures_ticker 触发 -1003
        self.ticker_book = None
        # 持仓模式: False=单向(BOTH+reduceOnly), True=双向(LONG/SHORT, 无 reduceOnly)
        self.hedge_mode: bool = False
        # paper 下单用：由 WS mark 回调写入，避免再打 REST markPrice
        self._mark_prices: dict[str, float] = {}
        # symbol -> 当前杠杆（跟随交易所模式缓存）
        self._leverage_cache: dict[str, int] = {}

    async def init(self):
        if not self.api_key or not self.api_secret:
            logger.warning("未配置 API Key，仅支持公开数据接口（K线）")
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                self._client = await AsyncClient.create(
                    self.api_key or None,
                    self.api_secret or None,
                    testnet=self.testnet,
                )
                break
            except (asyncio.TimeoutError, TimeoutError, OSError, ConnectionError) as e:
                last_err = e
                logger.warning("连接币安超时/失败 (%s/%s): %s", attempt, 3, type(e).__name__)
                if attempt < 3:
                    await asyncio.sleep(1.5 * attempt)
            except Exception as e:
                last_err = e
                raise
        else:
            raise RuntimeError(
                "连接币安 API 超时（已重试 3 次）。请检查本机网络/代理/防火墙，"
                "确认可访问 https://fapi.binance.com"
            ) from last_err
        await self._load_exchange_info()
        await self._detect_position_mode()

    async def _detect_position_mode(self):
        if not self.api_key:
            return
        try:
            info = await self._client.futures_get_position_mode()
            self.hedge_mode = bool(info.get("dualSidePosition"))
            logger.info("持仓模式: %s", "双向 Hedge" if self.hedge_mode else "单向 One-way")
        except Exception as e:
            logger.warning("检测持仓模式失败，按单向处理: %s", e)
            self.hedge_mode = False

    async def close(self):
        if self._client:
            await self._client.close_connection()

    async def _load_exchange_info(self):
        """加载所有 USDT 合约的下单精度，避免下单时报错。"""
        try:
            info = await self._client.futures_exchange_info()
        except Exception as e:
            logger.error("获取 exchangeInfo 失败: %s", e)
            return
        perpetuals: list[str] = []
        for s in info.get("symbols", []):
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            self._symbol_filters[s["symbol"]] = {
                "tick_size": float(filters.get("PRICE_FILTER", {}).get("tickSize", "0.01")),
                "step_size": float(filters.get("LOT_SIZE", {}).get("stepSize", "0.001")),
                "min_qty": float(filters.get("LOT_SIZE", {}).get("minQty", "0.001")),
                "min_notional": float(
                    filters.get("MIN_NOTIONAL", {}).get("notional")
                    or filters.get("MIN_NOTIONAL", {}).get("minNotional")
                    or filters.get("NOTIONAL", {}).get("minNotional")
                    or filters.get("NOTIONAL", {}).get("notional")
                    or "20"
                ),
                "quantity_precision": s.get("quantityPrecision", 3),
                "price_precision": s.get("pricePrecision", 2),
            }
            if (
                s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
            ):
                perpetuals.append(s["symbol"])
        self._usdt_perpetuals = perpetuals
        logger.info("exchangeInfo 缓存: %d 个 USDT 永续", len(perpetuals))

    def update_mark_price(self, symbol: str, mark: float):
        if mark > 0:
            self._mark_prices[symbol] = mark

    def round_qty(self, symbol: str, qty: float) -> float:
        f = self._symbol_filters.get(symbol)
        if not f:
            return round(qty, 3)
        step = f["step_size"]
        if step <= 0:
            return qty
        # 向下取整到 step_size 的整数倍
        precision = f["quantity_precision"]
        rounded = (int(qty / step)) * step
        return round(rounded, precision)

    def min_qty(self, symbol: str) -> float:
        return self._symbol_filters.get(symbol, {}).get("min_qty", 0.001)

    def min_notional(self, symbol: str) -> float:
        """币安最小名义价值，开仓不到这个数会被拒（-4164）。"""
        return self._symbol_filters.get(symbol, {}).get("min_notional", 20.0)

    # ---------------- 公开接口 ----------------
    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """返回最近 limit 根 K 线 [open_time, open, high, low, close, volume, ...]"""
        return await self._client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    async def get_24h_tickers(self) -> list[dict]:
        """优先 WS TickerBook；无缓存时才打 REST（易触发 -1003）。"""
        book = self.ticker_book
        if book is not None:
            if book.size == 0:
                await book.wait_ready(timeout=25)
            rows = book.get_24h_tickers()
            if rows:
                return rows
        logger.warning("TickerBook 不可用，回退 REST futures_ticker（可能限流）")
        return await self._client.futures_ticker()

    async def get_usdt_perpetual_symbols(self) -> list[str]:
        """优先内存缓存 / TickerBook，避免反复打 exchangeInfo。"""
        if self._usdt_perpetuals:
            return list(self._usdt_perpetuals)
        book = self.ticker_book
        if book is not None and book.size > 0:
            return book.get_usdt_symbols()
        try:
            await self._load_exchange_info()
        except Exception as e:
            logger.error("获取 exchangeInfo 失败: %s", e)
        if self._usdt_perpetuals:
            return list(self._usdt_perpetuals)
        return list(self._symbol_filters.keys())

    async def get_mark_price(self, symbol: str) -> float:
        cached = self._mark_prices.get(symbol)
        if cached and cached > 0:
            return cached
        data = await self._client.futures_mark_price(symbol=symbol)
        mark = float(data["markPrice"])
        self._mark_prices[symbol] = mark
        return mark

    # ---------------- 账户配置 ----------------
    async def setup_symbol(self, symbol: str, leverage: int, margin_type: str = "CROSSED",
                           *, set_leverage: bool = True):
        """配置保证金模式；可选设置杠杆。已是相同设置时币安返回错误，吞掉即可。"""
        try:
            await self._client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except BinanceAPIException as e:
            if e.code != -4046:  # "No need to change margin type"
                logger.warning("setMarginType %s: %s", symbol, e)
        if set_leverage:
            try:
                await self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
                self._leverage_cache[symbol] = int(leverage)
            except BinanceAPIException as e:
                logger.warning("setLeverage %s: %s", symbol, e)

    async def get_symbol_leverage(self, symbol: str, default: int = 20) -> int:
        """读取交易所该币当前杠杆（优先缓存）。"""
        cached = self._leverage_cache.get(symbol)
        if cached and cached > 0:
            return int(cached)
        if not self._client or self.paper:
            return int(default)
        try:
            rows = await self._client.futures_position_information(symbol=symbol)
            for row in rows or []:
                lev = int(float(row.get("leverage") or 0))
                if lev > 0:
                    self._leverage_cache[symbol] = lev
                    return lev
        except Exception as e:
            logger.warning("读取杠杆失败 %s: %s", symbol, e)
        return int(default)

    async def get_account_balance(self) -> float:
        """USDT 可用余额。"""
        try:
            for asset in await self._client.futures_account_balance():
                if asset["asset"] == "USDT":
                    return float(asset["availableBalance"])
        except Exception as e:
            logger.error("获取余额失败: %s", e)
        return 0.0

    # ---------------- 下单 ----------------
    @staticmethod
    def gen_client_order_id(prefix: str = "qt") -> str:
        """生成 clientOrderId，确保 ≤ 36 字符（币安硬限制）。"""
        ts_part = f"{int(time.time()*1000)}"       # 13 位
        uid_part = uuid.uuid4().hex[:6]             # 6 位
        # 格式: {prefix}-{ts}-{uid}，分隔符占 2 位
        # 最大 prefix 长度 = 36 - 2 - 13 - 6 = 15
        max_prefix = 36 - 2 - len(ts_part) - len(uid_part)
        prefix = prefix[:max_prefix]
        return f"{prefix}-{ts_part}-{uid_part}"

    async def market_order(self, symbol: str, side: str, position_side: str,
                           quantity: float, reduce_only: bool = False,
                           client_order_id: Optional[str] = None) -> dict:
        """
        市价单。side=BUY/SELL, position_side=LONG/SHORT。
        自动适配两种持仓模式：
        - 单向 (One-way)  → positionSide="BOTH" + reduceOnly
        - 双向 (Hedge)    → positionSide=LONG/SHORT，不能传 reduceOnly
        """
        if self.paper:
            mark = await self.get_mark_price(symbol)
            return {
                "paper": True, "symbol": symbol, "side": side, "quantity": quantity,
                "avgPrice": mark,
                "clientOrderId": client_order_id or self.gen_client_order_id("paper"),
                "status": "FILLED",
            }
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": client_order_id or self.gen_client_order_id(),
        }
        if self.hedge_mode:
            # 双向：positionSide 决定开/平，不能用 reduceOnly
            params["positionSide"] = position_side
        else:
            # 单向：positionSide 必须 BOTH，reduceOnly 决定开/平
            params["positionSide"] = "BOTH"
            if reduce_only:
                params["reduceOnly"] = "true"
        try:
            res = await self._client.futures_create_order(**params)
            try:
                detail = await self._client.futures_get_order(symbol=symbol, orderId=res["orderId"])
                res["avgPrice"] = float(detail.get("avgPrice") or 0)
            except Exception:
                res["avgPrice"] = 0.0
            return res
        except BinanceAPIException as e:
            logger.error("下单失败 %s %s qty=%s: %s", symbol, side, quantity, e)
            raise

    async def close_position(self, symbol: str, direction: str, quantity: float,
                             client_order_id: Optional[str] = None) -> dict:
        """平掉指定数量持仓。direction=LONG → SELL；direction=SHORT → BUY。"""
        side = "SELL" if direction == "LONG" else "BUY"
        return await self.market_order(symbol, side=side, position_side=direction,
                                       quantity=quantity, reduce_only=True,
                                       client_order_id=client_order_id)

    async def get_position(self, symbol: str, position_side: Optional[str] = None) -> dict:
        """
        - 单向模式：返回 positionSide=BOTH 的记录（忽略 position_side 参数）
        - 双向模式：返回 positionSide=LONG 或 SHORT 的非零持仓；如果都有非零，需要指定 position_side
        """
        positions = await self._client.futures_position_information(symbol=symbol)
        candidates = [p for p in positions if p["symbol"] == symbol]
        if not candidates:
            return {}
        if not self.hedge_mode:
            for p in candidates:
                if p.get("positionSide", "BOTH") == "BOTH":
                    return p
            return candidates[0]
        # hedge: 优先返回有持仓的；否则按 position_side 选
        non_zero = [p for p in candidates if float(p.get("positionAmt") or 0) != 0]
        if position_side:
            for p in candidates:
                if p.get("positionSide") == position_side:
                    return p
        if non_zero:
            return non_zero[0]
        return candidates[0]

    async def get_open_positions(self) -> list[dict]:
        """返回账户下所有非零持仓（不按监控列表过滤）。paper 返回空。"""
        if self.paper or not self._client:
            return []
        positions = await self._client.futures_position_information()
        out = []
        for p in positions:
            try:
                if abs(float(p.get("positionAmt") or 0)) > 0:
                    out.append(p)
            except Exception:
                continue
        return out
