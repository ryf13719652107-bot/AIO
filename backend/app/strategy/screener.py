"""
动态选币：CoinGecko 市值 + 币安 24h 成交额/价格 + 120 分钟 RSI(6) 区间。
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import get_settings
from app.schemas import ScreeningConfig, StrategyParams
from app.strategy.indicators import RSIState

logger = logging.getLogger(__name__)


@dataclass
class SymbolScreenResult:
    symbol: str
    base_asset: str
    price: float
    volume_24h_usd: float
    market_cap_usd: Optional[float]


class SymbolScreener:
    """每小时刷新合格币种集合。"""

    def __init__(self):
        self.settings = get_settings()
        self._mcap_by_base: dict[str, float] = {}
        self._mcap_loaded = False

    async def _load_coingecko_mcap(self, client: httpx.AsyncClient, force: bool = False):
        """分页拉取 CoinGecko 市值，按 base symbol 取最大市值。"""
        if not force and self._mcap_loaded and self._mcap_by_base:
            return
        base_url = self.settings.COINGECKO_API_BASE.rstrip("/")
        merged: dict[str, float] = {}
        for page in range(1, 6):
            try:
                resp = await client.get(
                    f"{base_url}/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": 250,
                        "page": page,
                        "sparkline": "false",
                    },
                    timeout=self.settings.COINGECKO_TIMEOUT,
                )
                resp.raise_for_status()
                rows = resp.json()
                if not rows:
                    break
                for row in rows:
                    sym = (row.get("symbol") or "").upper()
                    cap = row.get("market_cap")
                    if sym and cap:
                        merged[sym] = max(float(cap), merged.get(sym, 0.0))
            except Exception as e:
                logger.warning("CoinGecko page %d 失败: %s", page, e)
                break
        self._mcap_by_base = merged
        self._mcap_loaded = True
        logger.info("CoinGecko 市值缓存: %d 个 base symbol", len(merged))

    @staticmethod
    def _base_from_symbol(symbol: str) -> str:
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol

    @staticmethod
    async def _rsi_2h(exchange_client, symbol: str, limit: int = 31) -> Optional[float]:
        """最近已收盘 2h K 的 RSI(6)（去掉最后一根未收盘 K；失败返回 None 不拦截）。"""
        try:
            klines = await exchange_client.get_klines(symbol, "2h", limit)
        except Exception as e:
            logger.warning("2h K 线拉取失败 %s: %s", symbol, e)
            return None
        rsi = RSIState(period=6)
        value: Optional[float] = None
        for k in klines[:-1]:  # 最后一根未收盘，不参与
            try:
                value = rsi.update(float(k[4]))
            except (TypeError, ValueError, IndexError):
                return None
        return value

    async def _rsi_2h_filter(self, exchange_client, cfg: ScreeningConfig,
                             results: list[SymbolScreenResult]) -> list[SymbolScreenResult]:
        """120 分钟 RSI(6) 区间筛选：≥ 上限阈值 或 ≤ 下限阈值（启用项取 OR）。"""
        keep: list[SymbolScreenResult] = []
        for r in results:
            rsi = await self._rsi_2h(exchange_client, r.symbol)
            if rsi is None:
                keep.append(r)  # 数据不可得不拦截（下轮刷新重试）
                continue
            ok_high = cfg.enable_rsi_2h_high and rsi >= cfg.rsi_2h_high_threshold
            ok_low = cfg.enable_rsi_2h_low and rsi <= cfg.rsi_2h_low_threshold
            if ok_high or ok_low:
                keep.append(r)
            else:
                logger.debug("RSI 筛选剔除 %s: 2h RSI(6)=%.1f", r.symbol, rsi)
        return keep

    async def screen(self, exchange_client, params: StrategyParams, *, refresh_mcap: bool = False) -> list[SymbolScreenResult]:
        cfg: ScreeningConfig = params.screening
        tickers = await exchange_client.get_24h_tickers()
        usdt_symbols = set(await exchange_client.get_usdt_perpetual_symbols())

        async with httpx.AsyncClient() as client:
            if cfg.enable_mcap:
                await self._load_coingecko_mcap(client, force=refresh_mcap)

        results: list[SymbolScreenResult] = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in usdt_symbols:
                continue
            price = float(t.get("lastPrice") or 0)
            quote_vol = float(t.get("quoteVolume") or 0)
            base = self._base_from_symbol(sym)
            mcap = self._mcap_by_base.get(base)

            if cfg.enable_volume:
                # 成交额需在 [下限, 上限] 内
                if quote_vol < cfg.volume_min_usd:
                    continue
                if quote_vol > cfg.volume_max_usd:
                    continue
            if cfg.enable_price and price >= cfg.price_max_usd:
                continue
            if cfg.enable_mcap:
                # 市值需在 [下限, 上限] 内；无市值数据时不拦截
                if mcap is None or mcap < cfg.mcap_min_usd:
                    continue
                if mcap > cfg.mcap_max_usd:
                    continue

            results.append(SymbolScreenResult(
                symbol=sym,
                base_asset=base,
                price=price,
                volume_24h_usd=quote_vol,
                market_cap_usd=mcap,
            ))

        results.sort(key=lambda x: x.volume_24h_usd, reverse=True)
        if cfg.enable_rsi_2h_high or cfg.enable_rsi_2h_low:
            results = await self._rsi_2h_filter(exchange_client, cfg, results)
            logger.info("选币筛选完成: %d 个合格币种（含 2h RSI 区间筛选）", len(results))
        else:
            logger.info("选币筛选完成: %d 个合格币种", len(results))
        return results
