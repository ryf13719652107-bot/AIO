"""
策略主引擎 — 多周期 EMA/MACD/RSI 策略（v3，双向持仓）。

- K 线订阅 1m/5m/15m/30m/1h/2h；信号周期固定 5m（收盘判定）
- 开仓边沿触发（防启动追单：启动后首次评估仅记录状态）
- 首仓 + 5 个加仓档（回踩 mark vs 5m EMA7/25/99 + 15m/30m 趋势），各档仅一次
- TP1-6 全部收盘判定；TP6 分批后武装保本损（mark 触及开仓均价全平）
- 规则2：开仓时快照策略参数，旧仓按快照管理
- 规则3：同币多空双向持仓并存
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections import deque
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from binance.exceptions import BinanceAPIException
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.exchange.binance_rest import BinanceFuturesClient
from app.exchange.binance_ws import BinanceFuturesWS
from app.exchange.binance_poller import BinanceFuturesPoller
from app.exchange.binance_ticker_ws import BinanceTickerBook
from app.models import EngineState, PositionLog, PositionState, StrategyConfig, Trade
from app.schemas import (
    SIGNAL_TIMEFRAME, TIMEFRAMES, GlobalConfig, StrategyConfigPayload,
    StrategyParams, SymbolConfig,
)
from app.strategy.indicators import SymbolIndicators
from app.strategy.position import SymbolPosition
from app.strategy.screener import SymbolScreener
from app.strategy.signal import Action, StrategyRules

logger = logging.getLogger(__name__)

TZ_CN = ZoneInfo("Asia/Shanghai")
TF_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
}
BOOTSTRAP_LIMIT = 200
BOOTSTRAP_CONCURRENCY = 2
BOOTSTRAP_PAUSE_SEC = 0.2
BOOTSTRAP_SYMBOL_CONCURRENCY = 2
DIRECTIONS = ("LONG", "SHORT")
CLOSE_EVENTS = (
    "CLOSE_TP1", "CLOSE_TP2", "CLOSE_TP3", "CLOSE_TP4", "CLOSE_TP5", "CLOSE_TP6",
    "CLOSE_BREAKEVEN", "CLOSE_MANUAL", "CLOSE_EXTERNAL",
)


class EventBus:
    def __init__(self, maxlen: int = 500):
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque = deque(maxlen=maxlen)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def history(self) -> list:
        return list(self._history)

    def publish(self, event: dict):
        if "ts" not in event or not event.get("ts"):
            event = {**event, "ts": datetime.now(TZ_CN).isoformat(timespec="seconds")}
        else:
            event = dict(event)
        self._history.append(event)
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except Exception:
                    pass
            try:
                q.put_nowait(event)
            except Exception:
                pass


class StrategyEngine:
    _instance: Optional["StrategyEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.settings = get_settings()
        self.bus = EventBus()
        self.running = False
        self.started_at: Optional[datetime] = None

        self.symbols: list[str] = []
        self.cfg_global = GlobalConfig(
            capital_source=self.settings.CAPITAL_SOURCE,
            capital_usdt=self.settings.CAPITAL_USDT,
        )
        self.cfg_strategy = StrategyParams(leverage=self.settings.LEVERAGE)
        self.cfg_symbols: dict[str, SymbolConfig] = {}

        self.indicators: dict[str, SymbolIndicators] = {}
        # symbol → {"LONG": SymbolPosition, "SHORT": SymbolPosition}
        self.positions: dict[str, dict[str, SymbolPosition]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

        self.exchange: Optional[BinanceFuturesClient] = None
        self.ws: Optional[BinanceFuturesWS] = None
        self.poller: Optional[BinanceFuturesPoller] = None
        self.ticker_book: Optional[BinanceTickerBook] = None
        self.screener = SymbolScreener()

        self.exchange_api_key: str = self.settings.BINANCE_API_KEY or ""
        self.exchange_api_secret: str = self.settings.BINANCE_API_SECRET or ""
        self.paper_trading: bool = bool(self.settings.PAPER_TRADING)
        self.binance_testnet: bool = bool(self.settings.BINANCE_TESTNET)

        self._position_sync_task: Optional[asyncio.Task] = None
        self._screener_task: Optional[asyncio.Task] = None
        self._boot_task: Optional[asyncio.Task] = None
        self.booting: bool = False
        self._bootstrap_last_open: dict[tuple[str, str], int] = {}
        self._last_screen_at: Optional[datetime] = None
        self._screen_count: int = 0
        self._tf_eval_sem = asyncio.Semaphore(12)
        self._last_kline_open: dict[tuple[str, str], int] = {}
        self._rest_banned_until: float = 0.0
        self._backfill_lock = asyncio.Lock()
        self._backfill_task: Optional[asyncio.Task] = None
        self._run_epoch: int = 0
        self._mark_busy: set[str] = set()
        self._last_entry_cond: dict[tuple[str, str], bool] = {}  # 开仓边沿记忆

    # ---------------- 配置 ----------------
    def _feed_intervals(self) -> list[str]:
        """全部策略周期。"""
        return list(TIMEFRAMES)

    async def load_config_from_db(self):
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(StrategyConfig).where(StrategyConfig.name == "default"))
            row = res.scalar_one_or_none()
            if row:
                payload = self._normalize_config_payload(row.payload)
                self.apply_config(StrategyConfigPayload.model_validate(payload))

    @staticmethod
    def _normalize_config_payload(raw: dict) -> dict:
        """旧版（v1/v2）配置迁移为 v3；返回可校验的干净 payload。"""
        data = dict(raw or {})
        strategy = dict(data.get("strategy") or {})
        version = strategy.get("strategy_version")
        try:
            version_i = int(version) if version is not None else 1
        except (TypeError, ValueError):
            version_i = 1

        if version_i >= 3:
            # 旧 v3 合并开关拆分（保留用户勾选状态）：
            #   macd → macd_dif / macd_hist
            #   ema_15m/30m/60m/120m → ema_XX_cross / ema_XX_over99
            entry = dict(strategy.get("entry") or {})
            for side_key in ("long", "short"):
                side = dict(entry.get(side_key) or {})
                if isinstance(side.get("macd"), bool):
                    old = side.pop("macd")
                    side.setdefault("macd_dif", old)
                    side.setdefault("macd_hist", old)
                for tf in ("15m", "30m", "60m", "120m"):
                    key = f"ema_{tf}"
                    if isinstance(side.get(key), bool):
                        old = side.pop(key)
                        side.setdefault(f"ema_{tf}_cross", old)
                        side.setdefault(f"ema_{tf}_over99", old)
                if side:
                    entry[side_key] = side
            if entry:
                strategy = {**strategy, "entry": entry}
            clean = StrategyParams.model_validate(strategy).model_dump()
            data["strategy"] = clean
        else:
            # v1/v2 → v3：策略参数全部用默认值，仅保留筛选与杠杆设置
            defaults = StrategyParams().model_dump()
            old_screening = strategy.get("screening")
            if isinstance(old_screening, dict):
                screening = dict(defaults["screening"])
                for key in screening:
                    if key in old_screening and old_screening[key] is not None:
                        screening[key] = old_screening[key]
                defaults["screening"] = screening
            if strategy.get("leverage") is not None:
                try:
                    defaults["leverage"] = int(strategy["leverage"])
                except (TypeError, ValueError):
                    pass
            if strategy.get("leverage_mode") in ("follow", "manual"):
                defaults["leverage_mode"] = strategy["leverage_mode"]
            data["strategy"] = defaults

        data.setdefault("symbols", data.get("symbols") or [])
        if "global" not in data and "global_" not in data:
            data["global"] = {}
        return StrategyConfigPayload.model_validate(data).model_dump(by_alias=True)

    async def save_config_to_db(self):
        payload = self.snapshot_config().model_dump(by_alias=True)
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(StrategyConfig).where(StrategyConfig.name == "default"))
            row = res.scalar_one_or_none()
            if row is None:
                row = StrategyConfig(name="default", payload=payload)
                db.add(row)
            else:
                row.payload = payload
            await db.commit()

    def apply_config(self, payload: StrategyConfigPayload):
        self.cfg_global = payload.global_
        self.cfg_strategy = payload.strategy
        for sc in payload.symbols:
            self.cfg_symbols[sc.symbol] = sc
            self._ensure_symbol(sc.symbol)
        # 运行中勿用前端不完整的 symbols 覆盖监控列表
        if payload.symbols and not (self.running or self.booting):
            self.symbols = [s.symbol for s in payload.symbols if s.enabled]

    def _ensure_symbol(self, symbol: str):
        if symbol not in self.indicators:
            self.indicators[symbol] = SymbolIndicators(symbol=symbol, timeframes=TIMEFRAMES)
        if symbol not in self.positions:
            self.positions[symbol] = {
                d: SymbolPosition(symbol=symbol) for d in DIRECTIONS
            }
        if symbol not in self.locks:
            self.locks[symbol] = asyncio.Lock()

    def snapshot_config(self) -> StrategyConfigPayload:
        return StrategyConfigPayload(
            global_=self.cfg_global,
            strategy=self.cfg_strategy,
            symbols=list(self.cfg_symbols.values()),
        )

    @staticmethod
    async def _kv_set(key: str, value: str):
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(EngineState).where(EngineState.key == key))
            row = res.scalar_one_or_none()
            if row is None:
                db.add(EngineState(key=key, value=value))
            else:
                row.value = value
            await db.commit()

    @staticmethod
    async def _kv_get(key: str) -> Optional[str]:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(EngineState).where(EngineState.key == key))
            row = res.scalar_one_or_none()
            return row.value if row else None

    async def load_exchange_settings(self):
        """从 DB 加载交易所配置，缺省回退 .env。"""
        key = await self._kv_get("binance_api_key")
        secret = await self._kv_get("binance_api_secret")
        paper = await self._kv_get("paper_trading")
        testnet = await self._kv_get("binance_testnet")
        self.exchange_api_key = key if key is not None else (self.settings.BINANCE_API_KEY or "")
        self.exchange_api_secret = secret if secret is not None else (self.settings.BINANCE_API_SECRET or "")
        if paper is not None:
            self.paper_trading = paper.strip().lower() in ("1", "true", "yes", "on")
        else:
            self.paper_trading = bool(self.settings.PAPER_TRADING)
        if testnet is not None:
            self.binance_testnet = testnet.strip().lower() in ("1", "true", "yes", "on")
        else:
            self.binance_testnet = bool(self.settings.BINANCE_TESTNET)

    @staticmethod
    def _mask_key(key: str) -> str:
        k = (key or "").strip()
        if not k:
            return ""
        if len(k) <= 8:
            return "*" * len(k)
        return f"{k[:4]}{'*' * max(4, len(k) - 8)}{k[-4:]}"

    async def get_exchange_settings_view(self) -> dict:
        await self.load_exchange_settings()
        key = self.exchange_api_key or ""
        return {
            "api_key": key,
            "api_key_masked": self._mask_key(key),
            "has_api_key": bool(key.strip()),
            "has_api_secret": bool((self.exchange_api_secret or "").strip()),
            "paper_trading": self.paper_trading,
            "testnet": self.binance_testnet,
        }

    async def update_exchange_settings(self, payload) -> dict:
        """更新并持久化交易所配置。运行中禁止修改。"""
        if self.running or self.booting:
            raise RuntimeError("策略运行中，请先停止再修改 API 配置")
        await self.load_exchange_settings()
        if getattr(payload, "clear_credentials", False):
            await self._kv_set("binance_api_key", "")
            await self._kv_set("binance_api_secret", "")
            self.exchange_api_key = ""
            self.exchange_api_secret = ""
        else:
            if payload.api_key is not None:
                key = payload.api_key.strip()
                await self._kv_set("binance_api_key", key)
                self.exchange_api_key = key
            if payload.api_secret is not None and payload.api_secret.strip():
                secret = payload.api_secret.strip()
                await self._kv_set("binance_api_secret", secret)
                self.exchange_api_secret = secret
        if payload.paper_trading is not None:
            await self._kv_set("paper_trading", "true" if payload.paper_trading else "false")
            self.paper_trading = bool(payload.paper_trading)
        if payload.testnet is not None:
            await self._kv_set("binance_testnet", "true" if payload.testnet else "false")
            self.binance_testnet = bool(payload.testnet)
        return await self.get_exchange_settings_view()

    async def was_running_before(self) -> bool:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(EngineState).where(EngineState.key == "running"))
            row = res.scalar_one_or_none()
            return row.value == "true" if row else False

    # ---------------- 开仓/加仓许可 ----------------
    def allow_open_for(self, symbol: str) -> bool:
        cfg = self.cfg_symbols.get(symbol)
        return cfg is None or cfg.enabled

    def allow_increase_for(self, symbol: str) -> bool:
        cfg = self.cfg_symbols.get(symbol)
        if cfg is not None and not cfg.enabled:
            return False
        return True

    # ---------------- 仓位参数快照（规则2） ----------------
    def _params_for(self, pos: SymbolPosition) -> StrategyParams:
        """旧仓按开仓时快照管理；无快照用当前配置。"""
        snap = pos.params_snapshot
        if snap:
            try:
                return StrategyParams.model_validate(snap)
            except Exception:
                logger.warning("仓位参数快照解析失败，回退当前配置")
        return self.cfg_strategy

    def _order_margin_for(self, pos: Optional[SymbolPosition] = None) -> float:
        """每单保证金：加仓用仓位快照，新单用当前配置。"""
        if pos is not None and pos.params_snapshot:
            plan = pos.params_snapshot.get("order_plan") or {}
            m = plan.get("order_margin_usdt")
            if isinstance(m, (int, float)) and m > 0:
                return float(m)
        return float(self.cfg_strategy.order_plan.order_margin_usdt)

    # ---------------- 持仓持久化 ----------------
    async def _persist_position(self, symbol: str):
        legs = self.positions.get(symbol)
        if legs is None:
            return
        for direction, pos in legs.items():
            if pos.is_flat:
                await self._delete_position_state(symbol, direction)
                continue
            async with AsyncSessionLocal() as db:
                res = await db.execute(
                    select(PositionState).where(
                        PositionState.symbol == symbol,
                        PositionState.direction == direction,
                    )
                )
                row = res.scalar_one_or_none()
                if row is None:
                    row = PositionState(symbol=symbol, direction=direction)
                    db.add(row)
                row.state_json = pos.to_dict()
                row.updated_at = datetime.utcnow()
                await db.commit()

    async def _delete_position_state(self, symbol: str, direction: Optional[str] = None):
        async with AsyncSessionLocal() as db:
            stmt = delete(PositionState).where(PositionState.symbol == symbol)
            if direction:
                stmt = stmt.where(PositionState.direction == direction)
            await db.execute(stmt)
            await db.commit()

    async def _load_position_states(self):
        """启动时从 DB 恢复仓位状态（在 adopt 之前）。"""
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(PositionState))
            rows = list(res.scalars().all())

        restored = 0
        for row in rows:
            if not row.symbol or row.direction not in DIRECTIONS:
                continue
            sym = row.symbol
            self._ensure_symbol(sym)
            if sym not in self.cfg_symbols:
                self.cfg_symbols[sym] = SymbolConfig(symbol=sym, enabled=True)
            if sym not in self.symbols:
                self.symbols.append(sym)
            try:
                state = dict(row.state_json or {})
            except Exception:
                state = {}
            pos = SymbolPosition.from_dict(state)
            pos.symbol = sym
            pos.direction = row.direction
            if pos.entry_price and not pos.mark_price:
                pos.mark_price = pos.entry_price
            self.positions[sym][row.direction] = pos
            restored += 1

        if restored:
            await self._log("INFO", "engine", f"已从 DB 恢复 {restored} 个仓位状态")

    # ---------------- 选币 ----------------
    async def _run_screen_and_sync(self, initial: bool = False):
        if not self.exchange:
            return
        try:
            results = await self.screener.screen(
                self.exchange, self.cfg_strategy, refresh_mcap=not initial,
            )
            if not results and (
                self.cfg_strategy.screening.enable_mcap
                or self.cfg_strategy.screening.enable_volume
            ):
                await self._log(
                    "WARN", "screener",
                    "CoinGecko 市值筛选无结果，降级为仅按成交额+价格筛选",
                )
                cfg = self.cfg_strategy.model_copy(deep=True)
                cfg.screening.enable_mcap = False
                results = await self.screener.screen(self.exchange, cfg, refresh_mcap=False)

            self._last_screen_at = datetime.now(TZ_CN)
            self._screen_count += 1

            screened = [r.symbol for r in results]
            holding_mem = [
                s for s, legs in self.positions.items()
                if any(not p.is_flat for p in legs.values())
            ]
            holding_ex = await self._exchange_holding_symbols()
            new_symbols = list(dict.fromkeys(holding_mem + holding_ex + screened))
            prev_symbols = set(self.symbols)

            for sym in new_symbols:
                self._ensure_symbol(sym)
                if sym not in self.cfg_symbols:
                    self.cfg_symbols[sym] = SymbolConfig(symbol=sym, enabled=True)

            removed = set(self.symbols) - set(new_symbols)
            for sym in removed:
                legs = self.positions.get(sym)
                if legs and all(p.is_flat for p in legs.values()):
                    self.cfg_symbols.pop(sym, None)

            self.symbols = new_symbols
            await self._adopt_exchange_positions(tag="选币同步")

            # 选币刷新后新进的币也要设杠杆（启动时只对当时列表设过一次）
            if not initial:
                added = [s for s in new_symbols if s not in prev_symbols]
                if added:
                    await self._configure_symbols_on_exchange(added)

            if initial:
                await self._bootstrap_all_symbols()
            else:
                for sym in new_symbols:
                    if not self._symbol_bootstrapped(sym):
                        await self._bootstrap_symbol(sym)

            await self._restart_market_feed()
            await self.save_config_to_db()
            await self._log(
                "INFO", "screener",
                f"选币刷新: {len(new_symbols)} 个币种 (信号周期={SIGNAL_TIMEFRAME})",
            )
        except Exception as e:
            logger.exception("选币刷新失败: %s", e)
            await self._log("ERROR", "screener", f"选币刷新失败: {e}")

    async def _screener_loop(self):
        refresh_sec = max(300, int(self.cfg_strategy.screening.refresh_hours * 3600))
        while self.running:
            try:
                await asyncio.sleep(refresh_sec)
                if not self.running:
                    break
                await self._run_screen_and_sync(initial=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("screener loop: %s", e)

    # ---------------- 生命周期 ----------------
    async def start(self, persist: bool = True):
        """快速返回：选币/bootstrap 放到后台，避免前端超时。"""
        if self.running or self.booting:
            return
        self.booting = True
        try:
            await self.load_config_from_db()
            await self.load_exchange_settings()
            try:
                await self._init_exchange()
            except (asyncio.TimeoutError, TimeoutError) as e:
                raise RuntimeError(
                    "连接币安 API 超时。请检查网络/代理，确认可访问 fapi.binance.com"
                ) from e
            self._run_epoch += 1
            # 规则1：每次接入（启动）清空开仓边沿记忆，重新记录条件状态，
            # 需条件由不满足变为满足才开单（防启动追单）
            self._last_entry_cond.clear()
            self.running = True
            self.started_at = datetime.now(TZ_CN)
            if persist:
                await self._kv_set("running", "true")
            self.bus.publish({
                "type": "engine.status", "running": True, "booting": True,
                "started_at": self.started_at.isoformat(),
            })
            await self._log(
                "INFO", "engine",
                f"引擎启动中：多周期 EMA/MACD/RSI 策略（v3 双向持仓），"
                f"信号周期={SIGNAL_TIMEFRAME}…",
            )
            self._boot_task = asyncio.create_task(self._boot_pipeline(persist=persist))
        except Exception:
            self.booting = False
            self.running = False
            raise

    async def _boot_pipeline(self, persist: bool = True):
        try:
            # 先恢复 DB 仓位状态，再选币/对账交易所
            await self._load_position_states()
            await self._run_screen_and_sync(initial=True)
            if not self.running:
                return
            await self._configure_symbols_on_exchange()
            await self._sync_positions_with_exchange()
            await self._start_market_feed()
            self._position_sync_task = asyncio.create_task(self._periodic_position_sync())
            self._screener_task = asyncio.create_task(self._screener_loop())
            await self._log(
                "INFO", "engine",
                f"策略引擎已就绪 (paper={self.paper_trading}, "
                f"feed={self.settings.MARKET_FEED}, symbols={len(self.symbols)}, "
                f"lev_mode={self.cfg_strategy.leverage_mode})",
            )
            self.bus.publish({
                "type": "engine.status", "running": True, "booting": False,
                "started_at": self.started_at.isoformat() if self.started_at else None,
            })
            self.bus.publish({"type": "runtime", "data": self.runtime_snapshot()})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("启动流水线失败: %s", e)
            await self._log("ERROR", "engine", f"启动失败: {e}")
            await self.stop(persist=persist)
        finally:
            self.booting = False

    async def stop(self, persist: bool = True):
        if not self.running and not self.booting:
            return
        self.running = False
        self.booting = False
        self._run_epoch = getattr(self, "_run_epoch", 0) + 1
        self._mark_busy.clear()
        for task in (self._boot_task, self._position_sync_task, self._screener_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._boot_task = None
        self._position_sync_task = None
        self._screener_task = None
        if self.ws:
            await self.ws.stop()
        if self.poller:
            await self.poller.stop()
        if self.ticker_book:
            await self.ticker_book.stop()
        if self.exchange:
            await self.exchange.close()
        self.exchange = None
        self.ws = None
        self.poller = None
        self.ticker_book = None
        if persist:
            await self._kv_set("running", "false")
        await self._log("INFO", "engine", "策略引擎已停止")
        self.bus.publish({"type": "engine.status", "running": False, "booting": False})

    async def _init_exchange(self):
        await self.load_exchange_settings()
        self.exchange = BinanceFuturesClient(
            api_key=self.exchange_api_key,
            api_secret=self.exchange_api_secret,
            testnet=self.binance_testnet,
            paper=self.paper_trading,
        )
        await self.exchange.init()
        if not self.exchange.hedge_mode:
            await self._log(
                "WARN", "engine",
                "账户为「单向持仓」模式：新策略按双向持仓设计（同币多空并存），"
                "建议在币安开启「双向持仓」。",
            )
        self.ticker_book = BinanceTickerBook(testnet=self.binance_testnet)
        self.exchange.ticker_book = self.ticker_book
        await self.ticker_book.start()
        ok = await self.ticker_book.wait_ready(timeout=30)
        if ok:
            await self._log(
                "INFO", "screener",
                f"选币行情已切 WS ticker（{self.ticker_book.size} 个合约）",
            )
        else:
            await self._log("WARN", "screener", "WS ticker 未就绪，选币可能回退 REST（易限流）")
        mode = "模拟盘" if self.paper_trading else "实盘"
        await self._log(
            "INFO", "engine",
            f"交易所已初始化（{mode}，API Key={'已配置' if self.exchange_api_key else '未配置'}）",
        )
        try:
            if self.exchange and self.exchange._client:
                st = await self.exchange._client.get_server_time()
                server_ms = int(st["serverTime"])
                local_ms = int(time.time() * 1000)
                skew = local_ms - server_ms
                if abs(skew) > 3000:
                    await self._log(
                        "WARN", "engine",
                        f"本机时间与币安偏差 {skew/1000:.1f} 秒，请同步 Windows 时间",
                    )
        except Exception as e:
            logger.warning("校验币安时间失败: %s", e)

    async def _get_klines_safe(self, symbol: str, interval: str, limit: int = 200) -> list:
        """带 -1003 熔断的 K 线 REST 拉取。"""
        now = time.time()
        if now < self._rest_banned_until:
            raise RuntimeError(
                f"REST 限流熔断中，剩余 {self._rest_banned_until - now:.0f}s"
            )
        try:
            return await self.exchange.get_klines(symbol, interval, limit=limit)
        except BinanceAPIException as e:
            code = getattr(e, "code", None)
            msg = str(e)
            if code == -1003 or "Too many requests" in msg or "banned" in msg.lower():
                until = now + 60.0
                m = re.search(r"banned until (\d+)", msg, re.I)
                if m:
                    try:
                        until = max(until, int(m.group(1)) / 1000.0)
                    except Exception:
                        pass
                self._rest_banned_until = until
                await self._log(
                    "WARN", "engine",
                    f"币安 REST 限流 (-1003)，熔断至 "
                    f"{datetime.fromtimestamp(until, TZ_CN).strftime('%H:%M:%S')}",
                )
            raise
        except Exception:
            raise

    async def _bootstrap_symbol(self, sym: str):
        if not self.exchange:
            return
        if time.time() < self._rest_banned_until:
            await self._log("WARN", sym, "跳过 bootstrap：REST 熔断中")
            return
        ind = self.indicators[sym]
        try:
            for tf in self._feed_intervals():
                if time.time() < self._rest_banned_until:
                    break
                klines = await self._get_klines_safe(sym, tf, limit=BOOTSTRAP_LIMIT)
                if not klines:
                    continue
                closed = klines[:-1]
                closes = [float(k[4]) for k in closed]
                volumes = [float(k[5]) for k in closed]
                opens = [float(k[1]) for k in closed]
                ind.bootstrap_timeframe(tf, closes, volumes, opens)
                if closed:
                    ot = int(closed[-1][0])
                    self._bootstrap_last_open[(sym, tf)] = ot
                    self._last_kline_open[(sym, tf)] = ot
                await asyncio.sleep(BOOTSTRAP_PAUSE_SEC)
            await self._log("INFO", sym, f"指标 bootstrap 完成（{len(self._feed_intervals())} 个周期）")
        except Exception as e:
            logger.error("bootstrap %s: %s", sym, e)
            await self._log("ERROR", sym, f"bootstrap 失败: {e}")
        finally:
            if BOOTSTRAP_PAUSE_SEC > 0:
                await asyncio.sleep(BOOTSTRAP_PAUSE_SEC)

    def _symbol_bootstrapped(self, sym: str) -> bool:
        return all(
            (sym, tf) in self._bootstrap_last_open
            for tf in self._feed_intervals()
        )

    async def _bootstrap_all_symbols(self):
        sem = asyncio.Semaphore(BOOTSTRAP_SYMBOL_CONCURRENCY)

        async def _one(sym: str):
            if time.time() < self._rest_banned_until:
                return
            async with sem:
                await self._bootstrap_symbol(sym)

        await asyncio.gather(*[_one(sym) for sym in self.symbols])

    async def _backfill_klines_after_disconnect(self):
        """WS 重连后用 REST 回补缺口：只更新指标，不触发交易。"""
        if not self.exchange or not self.running:
            return
        if self._backfill_lock.locked():
            return
        async with self._backfill_lock:
            if time.time() < self._rest_banned_until:
                await self._log("WARN", "engine", "WS 回补跳过：REST 熔断中")
                return
            await self._log("INFO", "engine", "WS 重连，开始回补缺失 K 线…")
            filled = 0
            for sym in list(self.symbols):
                if time.time() < self._rest_banned_until:
                    break
                if sym not in self.indicators:
                    continue
                for tf in self._feed_intervals():
                    if time.time() < self._rest_banned_until:
                        break
                    last = (
                        self._last_kline_open.get((sym, tf))
                        or self._bootstrap_last_open.get((sym, tf), 0)
                    )
                    try:
                        klines = await self._get_klines_safe(sym, tf, limit=50)
                    except Exception as e:
                        logger.warning("回补 %s %s 失败: %s", sym, tf, e)
                        await asyncio.sleep(0.3)
                        continue
                    if not klines:
                        continue
                    closed = klines[:-1]
                    for row in closed:
                        try:
                            ot = int(row[0])
                        except Exception:
                            continue
                        if last and ot <= last:
                            continue
                        self.indicators[sym].update(
                            tf, float(row[4]), float(row[5]), float(row[1]),
                        )
                        self._last_kline_open[(sym, tf)] = ot
                        self._bootstrap_last_open[(sym, tf)] = ot
                        last = ot
                        filled += 1
                    await asyncio.sleep(BOOTSTRAP_PAUSE_SEC)
            await self._log("INFO", "engine", f"K 线回补完成，补入 {filled} 根（仅指标，不交易）")

    async def _on_ws_reconnect(self):
        if self._backfill_task and not self._backfill_task.done():
            return
        self._backfill_task = asyncio.create_task(
            self._backfill_klines_after_disconnect(),
            name="kline-backfill",
        )
        try:
            await self._backfill_task
        except Exception:
            pass

    async def _configure_symbols_on_exchange(self, symbols: Optional[list[str]] = None):
        if not self.exchange_api_key or not self.exchange_api_secret:
            logger.info("未配置 API Key，跳过杠杆/保证金模式设置")
            return
        set_lev = self.cfg_strategy.leverage_mode == "manual"
        targets = symbols if symbols is not None else self.symbols
        for sym in targets:
            cfg = self.cfg_symbols.get(sym)
            if cfg and not cfg.enabled:
                continue
            try:
                await self.exchange.setup_symbol(
                    sym, self.cfg_strategy.leverage, "CROSSED",
                    set_leverage=set_lev,
                )
            except Exception as e:
                logger.error("setup %s: %s", sym, e)

    async def _ensure_symbol_leverage(self, symbol: str):
        """手动杠杆模式下，下单前再确认该币杠杆（防选币后漏设 / 启动限流失败）。"""
        if (
            not self.exchange
            or not self.exchange_api_key
            or self.cfg_strategy.leverage_mode != "manual"
        ):
            return
        try:
            await self.exchange.setup_symbol(
                symbol, int(self.cfg_strategy.leverage), "CROSSED",
                set_leverage=True,
            )
        except Exception as e:
            logger.warning("ensure leverage %s: %s", symbol, e)

    async def _start_market_feed(self):
        mode = (self.settings.MARKET_FEED or "ws").lower()
        intervals = self._feed_intervals()
        if mode == "rest":
            kwargs = dict(
                client=self.exchange,
                symbols=self.symbols,
                intervals=intervals,
                on_kline_close=self._on_kline_close,
                on_mark_price=self._on_mark_price,
                mark_poll_sec=self.settings.MARK_POLL_SEC,
            )
            sig = inspect.signature(BinanceFuturesPoller.__init__).parameters
            if "on_kline_update" in sig:
                kwargs["on_kline_update"] = self._on_kline_update
            self.poller = BinanceFuturesPoller(**kwargs)
            self.poller._last_kline_open.update(self._bootstrap_last_open)
            await self.poller.start()
        else:
            self.ws = BinanceFuturesWS(
                symbols=self.symbols,
                intervals=intervals,
                on_kline_close=self._on_kline_close,
                on_mark_price=self._on_mark_price,
                testnet=self.binance_testnet,
                on_reconnect=self._on_ws_reconnect,
                on_kline_update=self._on_kline_update,
            )
            await self.ws.start()

    async def _restart_market_feed(self):
        if not self.running:
            return
        if self.ws:
            await self.ws.update_symbols(self.symbols)
        elif self.poller:
            await self.poller.restart(self.symbols)
            self.poller._last_kline_open.update(self._bootstrap_last_open)

    # ---------------- 持仓同步 ----------------
    async def _exchange_holding_symbols(self) -> list[str]:
        if not self.exchange or not self.exchange_api_key or self.paper_trading:
            return []
        try:
            rows = await self.exchange.get_open_positions()
        except Exception as e:
            logger.warning("拉取交易所持仓列表失败: %s", e)
            return []
        return list(dict.fromkeys(
            p.get("symbol") for p in rows if p.get("symbol")
        ))

    def _dir_from_exchange_row(self, row: dict) -> tuple[str, float, float]:
        raw_amt = float(row.get("positionAmt") or 0)
        qty = abs(raw_amt)
        entry = float(row.get("entryPrice") or 0)
        if self.exchange and self.exchange.hedge_mode:
            side = row.get("positionSide", "BOTH")
            if side in DIRECTIONS and qty > 0:
                return side, qty, entry
            return "FLAT", 0.0, 0.0
        if raw_amt > 0:
            return "LONG", qty, entry
        if raw_amt < 0:
            return "SHORT", qty, entry
        return "FLAT", 0.0, 0.0

    async def _margin_for_qty(self, symbol: str, qty: float, entry: float) -> float:
        if entry <= 0 or qty <= 0:
            return 0.0
        lev = await self._effective_leverage(symbol)
        lev = max(1, int(lev))
        return qty * entry / lev

    async def _adopt_exchange_positions(self, tag: str = "同步") -> list[str]:
        """把交易所非零仓位纳入监控（双向持仓逐腿 adopt）。"""
        if not self.exchange or not self.exchange_api_key or self.paper_trading:
            return []
        try:
            rows = await self.exchange.get_open_positions()
        except Exception as e:
            logger.warning("[%s] 拉取全量持仓失败: %s", tag, e)
            return []

        added: list[str] = []
        seen_open: set[tuple[str, str]] = set()
        by_sym: dict[str, list[tuple[str, float, float]]] = {}
        for row in rows:
            sym = row.get("symbol")
            if not sym:
                continue
            direction, qty, entry = self._dir_from_exchange_row(row)
            if direction == "FLAT" or qty <= 0:
                continue
            by_sym.setdefault(sym, []).append((direction, qty, entry))

        for sym, ex_legs in by_sym.items():
            self._ensure_symbol(sym)
            if sym not in self.cfg_symbols:
                self.cfg_symbols[sym] = SymbolConfig(symbol=sym, enabled=True)
            if sym not in self.symbols:
                self.symbols.append(sym)
                added.append(sym)

            for direction, qty, entry in ex_legs:
                seen_open.add((sym, direction))
                pos = self.positions[sym][direction]
                margin = await self._margin_for_qty(sym, qty, entry)
                if pos.is_flat:
                    # 交易所有仓、内存无仓（含 DB 未恢复）→ 接管并禁止加仓
                    pos.adopt(
                        direction, qty, entry, margin, block_add=True,
                        params_snapshot=self.cfg_strategy.model_dump(),
                    )
                    await self._persist_position(sym)
                    await self._log(
                        "WARN", sym,
                        f"[{tag}] 接管交易所持仓 {direction} qty={qty}（禁止加仓）",
                    )
                else:
                    changed = False
                    if abs(pos.quantity - qty) > 1e-8 or abs(pos.entry_price - entry) > 1e-10:
                        pos.quantity = qty
                        pos.entry_price = entry
                        changed = True
                    if margin > 0 and abs(pos.margin - margin) > max(1e-6, margin * 0.02):
                        pos.margin = margin
                        changed = True
                    if changed:
                        await self._persist_position(sym)

        # 内存有仓但交易所已平 → CLOSE_EXTERNAL
        for sym in list(self.symbols):
            legs = self.positions.get(sym)
            if not legs:
                continue
            for direction, pos in legs.items():
                if pos.is_flat or (sym, direction) in seen_open:
                    continue
                mark = pos.mark_price or pos.entry_price
                pnl = pos.unrealized_pnl(mark=mark) if mark else 0.0
                side = "SELL" if direction == "LONG" else "BUY"
                await self._record_trade(
                    sym, side, direction, "CLOSE_EXTERNAL",
                    pos.quantity, float(mark or 0), pos.margin,
                    f"ext-{sym.lower()[:10]}-{int(time.time())}",
                    {"external": True, "tag": tag},
                    realized_pnl=pnl,
                )
                await self._log(
                    "WARN", sym,
                    f"[{tag}] 交易所已无 {direction} 仓（可能强平/手动），记 CLOSE_EXTERNAL "
                    f"qty={pos.quantity} pnl={pnl:.2f}",
                )
                pos.close()
                await self._persist_position(sym)

        return added

    async def _sync_positions_with_exchange(self, periodic: bool = False):
        if not self.exchange or not self.exchange_api_key:
            return
        tag = "定期同步" if periodic else "启动同步"
        added = await self._adopt_exchange_positions(tag=tag)
        if added:
            for sym in added:
                if not self._symbol_bootstrapped(sym):
                    await self._bootstrap_symbol(sym)
            await self.save_config_to_db()
            if periodic and self.running:
                await self._restart_market_feed()
                self.bus.publish({"type": "runtime", "data": self.runtime_snapshot()})

    async def _periodic_position_sync(self):
        while True:
            try:
                await asyncio.sleep(10)
                if not self.running or not self.exchange:
                    break
                await self._sync_positions_with_exchange(periodic=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("定期持仓同步: %s", e)

    # ---------------- K 线 / Mark 回调 ----------------
    async def _on_kline_close(self, symbol: str, interval: str, k: dict):
        """任意周期收盘：更新指标并调度对应评估。"""
        x = k.get("x")
        if x is not True and str(x).lower() != "true":
            return
        if symbol not in self.indicators or interval not in TF_MS:
            return

        try:
            open_ms = int(k.get("t") or 0)
        except Exception:
            open_ms = 0

        key = (symbol, interval)
        prev_ot = self._last_kline_open.get(key)
        if open_ms and prev_ot is not None and open_ms <= prev_ot:
            return

        self.indicators[symbol].update(
            interval, float(k["c"]), float(k.get("v", 0) or 0),
            float(k.get("o", 0) or 0) or None,
        )
        if open_ms:
            self._last_kline_open[key] = open_ms
            self._bootstrap_last_open[key] = open_ms

        self.bus.publish({
            "type": "indicator",
            "symbol": symbol,
            "interval": interval,
            "snapshot": self.indicators[symbol].snapshot(),
        })

        asyncio.create_task(
            self._handle_interval_close(symbol, interval),
            name=f"tf-close-{symbol}-{interval}",
        )

    async def _handle_interval_close(self, symbol: str, interval: str):
        """周期收盘评估：TP（全周期）→ 趋势加仓（15m/30m）→ 开仓（5m）。"""
        if not self._trading_ready() or symbol not in self.indicators:
            return
        async with self._tf_eval_sem:
            async with self.locks.setdefault(symbol, asyncio.Lock()):
                if not self._trading_ready() or symbol not in self.indicators:
                    return
                ind = self.indicators[symbol]
                legs = self.positions[symbol]

                # 1) 止盈（TP1-6，收盘判定，全平优先于分批）
                for direction in DIRECTIONS:
                    pos = legs[direction]
                    if pos.is_flat:
                        continue
                    params = self._params_for(pos)
                    act = StrategyRules.check_exit(params, ind, pos, interval)
                    if act is not None and act.type != "NONE":
                        await self._execute_action(symbol, act, direction)

                # 2) 趋势加仓（15m/30m 收盘，各仅一次）
                if interval in ("15m", "30m") and self.allow_increase_for(symbol):
                    for direction in DIRECTIONS:
                        pos = legs[direction]
                        if pos.is_flat or pos.add_blocked:
                            continue
                        params = self._params_for(pos)
                        act = StrategyRules.trend_add_ready(params, ind, pos, interval)
                        if act is not None and act.type != "NONE":
                            await self._execute_action(symbol, act, direction)

                # 3) 开仓评估（仅 5m 收盘；边沿触发防追单）
                if interval == SIGNAL_TIMEFRAME:
                    await self._evaluate_entries(symbol, ind)

    async def _evaluate_entries(self, symbol: str, ind: SymbolIndicators):
        """5m 收盘开仓评估：已启用方向全关则不开；需 false→true 边沿。"""
        legs = self.positions[symbol]
        for direction in DIRECTIONS:
            ok, reason = StrategyRules.entry_ready(self.cfg_strategy, ind, direction)
            key = (symbol, direction)
            prev = self._last_entry_cond.get(key)
            self._last_entry_cond[key] = ok
            if not ok or prev is not False:
                # prev=None：启动后首次评估仅记录（规则1）；prev=True：无新边沿
                continue
            if not self.cfg_strategy.order_plan.enable_first:
                continue
            if not self.allow_open_for(symbol):
                continue
            if not legs[direction].is_flat:
                continue
            act = Action(
                type="OPEN_LONG" if direction == "LONG" else "OPEN_SHORT",
                reason=f"开{'多' if direction == 'LONG' else '空'}: {reason}",
            )
            try:
                await self._execute_action(symbol, act, direction)
            except Exception as e:
                logger.exception("开仓执行失败 %s %s: %s", symbol, direction, e)
                await self._log("ERROR", symbol, f"开仓执行失败: {e}")

    async def _on_kline_update(self, symbol: str, interval: str, k: dict):
        """未收盘 K 更新：仅刷新展示用 live 数据，不参与交易判定。"""
        if interval != SIGNAL_TIMEFRAME or symbol not in self.indicators:
            return
        x = k.get("x")
        if x is True or str(x).lower() == "true":
            return
        try:
            volume = float(k.get("v", 0) or 0)
            close_price = float(k.get("c", 0) or 0)
        except Exception:
            return
        self.indicators[symbol].set_live(
            interval, volume=volume, close=close_price or None,
        )
        self.bus.publish({
            "type": "indicator",
            "symbol": symbol,
            "interval": interval,
            "snapshot": self.indicators[symbol].snapshot(),
        })

    async def _on_mark_price(self, symbol: str, mark: float):
        if self.exchange:
            self.exchange.update_mark_price(symbol, mark)
        legs = self.positions.get(symbol)
        if legs is None:
            return
        for pos in legs.values():
            pos.mark_price = mark
        if not self._trading_ready() or mark <= 0:
            return
        if symbol in self._mark_busy:
            return
        self._mark_busy.add(symbol)
        asyncio.create_task(
            self._handle_mark_logic(symbol, mark),
            name=f"mark-logic-{symbol}",
        )

    async def _handle_mark_logic(self, symbol: str, mark: float):
        """实时盯价：回踩加仓（需已武装）+ 保本损（TP6 后武装）。"""
        try:
            async with self.locks.setdefault(symbol, asyncio.Lock()):
                if not self._trading_ready():
                    return
                legs = self.positions.get(symbol)
                ind = self.indicators.get(symbol)
                if not legs or ind is None:
                    return
                for direction in DIRECTIONS:
                    pos = legs[direction]
                    if pos.is_flat:
                        continue
                    params = self._params_for(pos)

                    # 回踩加仓档（mark 实时判定，各档仅一次）
                    if (
                        self.allow_increase_for(symbol)
                        and not pos.add_blocked
                        and not pos.breakeven_armed
                    ):
                        act = StrategyRules.check_pullback(params, ind, pos, mark)
                        if act is not None and act.type == "ADD":
                            await self._execute_action(symbol, act, direction)

                    # 保本损：mark 触及开仓均价 → 剩余全平
                    if pos.breakeven_armed and pos.quantity > 0:
                        hit = (
                            mark <= pos.entry_price
                            if direction == "LONG"
                            else mark >= pos.entry_price
                        )
                        if hit:
                            await self._do_close_leg(
                                symbol, direction, "CLOSE_BREAKEVEN",
                                reason=(
                                    f"保本损: mark={mark:.6f} "
                                    f"{'≤' if direction == 'LONG' else '≥'}"
                                    f"均价={pos.entry_price:.6f}"
                                ),
                            )
        except Exception as e:
            logger.exception("mark 逻辑失败 %s: %s", symbol, e)
            await self._log("ERROR", symbol, f"实时盯价失败: {e}")
        finally:
            self._mark_busy.discard(symbol)

    async def set_symbol_enabled(self, symbol: str, enabled: bool) -> None:
        """运行中可热更新：禁用只禁开仓/加仓，已有仓位继续止盈。"""
        symbol = (symbol or "").upper().strip()
        if not symbol:
            raise ValueError("币种为空")
        self._ensure_symbol(symbol)
        cfg = self.cfg_symbols.get(symbol)
        if cfg is None:
            cfg = SymbolConfig(symbol=symbol, enabled=enabled)
            self.cfg_symbols[symbol] = cfg
        else:
            cfg.enabled = enabled
        if enabled and symbol not in self.symbols:
            self.symbols.append(symbol)
        await self.save_config_to_db()
        state = "启用" if enabled else "禁开仓"
        await self._log(
            "INFO", symbol,
            f"手动{state}（运行中即时生效，持仓仍执行止盈止损）",
        )
        self.bus.publish({
            "type": "symbol.enabled",
            "symbol": symbol,
            "enabled": enabled,
        })
        self.bus.publish({"type": "runtime", "data": self.runtime_snapshot()})

    def _trading_ready(self) -> bool:
        return bool(self.running and self.exchange is not None)

    # ---------------- 决策执行 ----------------
    async def _execute_action(self, symbol: str, action: Action,
                              direction: Optional[str] = None):
        t = action.type
        if t == "OPEN_LONG":
            await self._do_open(symbol, "LONG", action.reason)
        elif t == "OPEN_SHORT":
            await self._do_open(symbol, "SHORT", action.reason)
        elif t == "ADD":
            if direction is None:
                logger.error("ADD 缺少方向: %s", symbol)
                return
            await self._do_add(symbol, direction, action.reason, action.trigger_key)
        elif t == "CLOSE_TP6":
            if direction is None:
                logger.error("CLOSE_TP6 缺少方向: %s", symbol)
                return
            await self._do_tp6(symbol, direction, action)
        elif t.startswith("CLOSE_"):
            if direction is None:
                logger.error("%s 缺少方向: %s", t, symbol)
                return
            await self._do_close_leg(symbol, direction, event=t, reason=action.reason)

    async def _capital_available(self) -> float:
        """当前可开新单的可用保证金（交易所可用 / 本金扣除已占用）。"""
        used = sum(
            p.margin
            for legs in self.positions.values()
            for p in legs.values()
            if not p.is_flat
        )
        if self.cfg_global.capital_source == "account" and self.exchange and self.exchange_api_key:
            try:
                bal = await self.exchange.get_account_balance()
                if self.paper_trading:
                    return max(0.0, float(bal) - used)
                return max(0.0, float(bal))
            except Exception as e:
                logger.warning("拉账户余额失败: %s", e)
        return max(0.0, self.cfg_global.capital_usdt - used)

    async def query_account_balance(self) -> dict:
        await self.load_exchange_settings()
        if not self.exchange_api_key or not self.exchange_api_secret:
            return {"available": None, "error": "未配置币安 API Key / Secret"}
        if self.exchange:
            try:
                bal = await self.exchange.get_account_balance()
                return {"available": bal, "error": None}
            except Exception as e:
                return {"available": None, "error": f"查询失败: {e}"}
        tmp = BinanceFuturesClient(
            api_key=self.exchange_api_key,
            api_secret=self.exchange_api_secret,
            testnet=self.binance_testnet,
            paper=False,
        )
        try:
            await tmp.init()
            return {"available": await tmp.get_account_balance(), "error": None}
        except Exception as e:
            return {"available": None, "error": f"查询失败: {e}"}
        finally:
            try:
                await tmp.close()
            except Exception:
                pass

    async def _effective_leverage(self, symbol: str) -> int:
        default = int(self.cfg_strategy.leverage or 10)
        if self.cfg_strategy.leverage_mode == "follow" and self.exchange:
            try:
                return await self.exchange.get_symbol_leverage(symbol, default=default)
            except Exception as e:
                logger.warning("读取杠杆失败 %s: %s", symbol, e)
                return default
        return default

    async def _calc_order_qty(self, symbol: str, margin: float) -> tuple[float, float]:
        if not self.exchange:
            raise RuntimeError("交易所未就绪")
        mark = await self.exchange.get_mark_price(symbol)
        leverage = await self._effective_leverage(symbol)
        notional = margin * leverage
        qty = self.exchange.round_qty(symbol, notional / mark if mark > 0 else 0)
        return qty, mark

    async def _do_open(self, symbol: str, direction: str, reason: str):
        if not self._trading_ready():
            return
        pos = self.positions[symbol][direction]
        if not pos.is_flat:
            return
        plan = self.cfg_strategy.order_plan
        margin = float(plan.order_margin_usdt)
        avail = await self._capital_available()
        if avail < margin:
            await self._log(
                "WARN", symbol,
                f"开仓跳过: 可用保证金 {avail:.4f} < 每单 {margin:.4f}",
            )
            return
        await self._ensure_symbol_leverage(symbol)
        qty, mark = await self._calc_order_qty(symbol, margin)

        if qty < self.exchange.min_qty(symbol):
            await self._log(
                "WARN", symbol,
                f"开仓跳过: 数量 {qty} < 最小 {self.exchange.min_qty(symbol)} "
                f"(保证金={margin:g}u)",
            )
            return
        if qty * mark < self.exchange.min_notional(symbol):
            await self._log(
                "WARN", symbol,
                f"开仓跳过: 名义≈{qty * mark:.2f} < 最小名义 "
                f"{self.exchange.min_notional(symbol):.2f} "
                f"(保证金={margin:g}u×杠杆，需调大 order_margin_usdt 或杠杆)",
            )
            return

        if not self._trading_ready():
            return
        side = "BUY" if direction == "LONG" else "SELL"
        coid = self.exchange.gen_client_order_id(prefix=f"open-{symbol.lower()[:8]}")
        order = await self.exchange.market_order(
            symbol, side=side, position_side=direction,
            quantity=qty, client_order_id=coid,
        )
        fill_price = float(order.get("avgPrice") or mark)
        if fill_price <= 0:
            fill_price = mark
        try:
            fill_qty = float(order.get("executedQty") or order.get("quantity") or qty)
        except Exception:
            fill_qty = qty
        if fill_qty <= 0:
            fill_qty = qty
        actual_margin = await self._margin_for_qty(symbol, fill_qty, fill_price)
        if actual_margin <= 0:
            actual_margin = margin
        # 规则2：开仓时快照当前策略参数
        pos.open(
            direction, fill_qty, fill_price, actual_margin,
            params_snapshot=self.cfg_strategy.model_dump(),
        )
        await self._persist_position(symbol)
        await self._record_trade(
            symbol, side, direction, "OPEN", fill_qty, fill_price, actual_margin, coid, order,
        )
        await self._log(
            "INFO", symbol,
            f"[开仓 {direction}] qty={fill_qty} @ {fill_price:.6f} "
            f"margin≈{actual_margin:.4f} | {reason}",
        )

    async def _do_add(self, symbol: str, direction: str, reason: str, trigger_key: str):
        if not self._trading_ready():
            return
        pos = self.positions[symbol][direction]
        if pos.is_flat or pos.add_blocked:
            await self._log(
                "WARN", symbol,
                f"加仓跳过: flat={pos.is_flat} blocked={pos.add_blocked} | {reason}",
            )
            return
        if pos.is_leg_fired(trigger_key):
            return
        margin = self._order_margin_for(pos)
        avail = await self._capital_available()
        if avail < margin:
            await self._log(
                "WARN", symbol,
                f"加仓跳过: 可用保证金 {avail:.4f} < 每单 {margin:.4f} | {reason}",
            )
            return
        await self._ensure_symbol_leverage(symbol)
        qty, mark = await self._calc_order_qty(symbol, margin)
        if qty < self.exchange.min_qty(symbol):
            await self._log(
                "WARN", symbol,
                f"加仓跳过: 数量 {qty} < 最小 {self.exchange.min_qty(symbol)} "
                f"(保证金={margin:g}u) | {reason}",
            )
            return
        if qty * mark < self.exchange.min_notional(symbol):
            await self._log(
                "WARN", symbol,
                f"加仓跳过: 名义≈{qty * mark:.2f} < 最小名义 "
                f"{self.exchange.min_notional(symbol):.2f} (保证金={margin:g}u) | {reason}",
            )
            return
        if not self._trading_ready():
            return
        side = "BUY" if direction == "LONG" else "SELL"
        coid = self.exchange.gen_client_order_id(prefix=f"add-{symbol.lower()[:8]}")
        order = await self.exchange.market_order(
            symbol, side=side, position_side=direction,
            quantity=qty, client_order_id=coid,
        )
        fill_price = float(order.get("avgPrice") or mark)
        if fill_price <= 0:
            fill_price = mark
        try:
            fill_qty = float(order.get("executedQty") or order.get("quantity") or qty)
        except Exception:
            fill_qty = qty
        if fill_qty <= 0:
            fill_qty = qty
        actual_margin = await self._margin_for_qty(symbol, fill_qty, fill_price)
        if actual_margin <= 0:
            actual_margin = margin
        pos.add(fill_qty, fill_price, actual_margin, trigger_key)
        await self._persist_position(symbol)
        await self._record_trade(
            symbol, side, direction, "ADD", fill_qty, fill_price, actual_margin, coid, order,
        )
        await self._log(
            "INFO", symbol,
            f"[加仓 {direction}/{trigger_key}] qty={fill_qty} @ {fill_price:.6f} "
            f"margin≈{actual_margin:.4f} 累计{pos.orders_count}单 | {reason}",
        )

    async def _do_tp6(self, symbol: str, direction: str, action: Action):
        """TP6 分批：标记该档已触发并武装保本损，再减仓。"""
        if not self._trading_ready():
            return
        pos = self.positions[symbol][direction]
        if pos.is_flat or pos.is_tp6_fired(action.trigger_key):
            return
        # 先标记（防重复触发），再下单
        pos.fire_tp6(action.trigger_key)
        await self._persist_position(symbol)
        await self._log("INFO", symbol, f"[TP6 触发] {direction}/{action.trigger_key} 保本损已武装")

        pct = float(action.close_pct)
        close_qty = pos.quantity * (pct / 100.0)
        remaining = pos.quantity - close_qty
        if remaining < self.exchange.min_qty(symbol):
            # 剩余过小 → 全平
            await self._do_close_leg(
                symbol, direction, event="CLOSE_TP6", reason=action.reason,
            )
            return

        coid = self.exchange.gen_client_order_id(prefix=f"tp6-{symbol.lower()[:8]}")
        order = await self.exchange.close_position(
            symbol, direction=direction, quantity=close_qty, client_order_id=coid,
        )
        fill_price = float(order.get("avgPrice") or pos.mark_price or pos.entry_price)
        if fill_price <= 0:
            fill_price = pos.entry_price
        if direction == "LONG":
            pnl = (fill_price - pos.entry_price) * close_qty
        else:
            pnl = (pos.entry_price - fill_price) * close_qty
        side = "SELL" if direction == "LONG" else "BUY"
        await self._record_trade(
            symbol, side, direction, "CLOSE_TP6", close_qty, fill_price,
            pos.margin * (pct / 100.0), coid, order, realized_pnl=pnl,
        )
        pos.partial_close(pct)
        await self._persist_position(symbol)
        await self._log(
            "INFO", symbol,
            f"[TP6 分批 {direction}] 平{pct:g}% qty={close_qty} @ {fill_price:.6f} "
            f"pnl={pnl:.2f} 剩余qty={pos.quantity:.8f} | {action.reason}",
        )

    async def _do_close_leg(self, symbol: str, direction: str, event: str,
                            reason: str):
        if not self.exchange or not self.running:
            return
        pos = self.positions[symbol][direction]
        if pos.is_flat:
            return
        if event not in CLOSE_EVENTS and not event.startswith("CLOSE_"):
            event = "CLOSE_MANUAL"
        qty = pos.quantity
        coid = self.exchange.gen_client_order_id(prefix=f"close-{symbol.lower()[:8]}")
        order = await self.exchange.close_position(
            symbol, direction=direction, quantity=qty, client_order_id=coid,
        )
        fill_price = float(order.get("avgPrice") or pos.mark_price or pos.entry_price)
        pnl = pos.unrealized_pnl(mark=fill_price)
        side = "SELL" if direction == "LONG" else "BUY"
        await self._record_trade(
            symbol, side, direction, event, qty, fill_price,
            pos.margin, coid, order, realized_pnl=pnl,
        )
        await self._log("INFO", symbol, f"[{event} {direction}] qty={qty} pnl={pnl:.2f} | {reason}")
        pos.close()
        await self._persist_position(symbol)

    async def close_all_positions(self, reason: str = "一键平仓") -> dict:
        """平掉内存中全部非空仓位（含双向腿）。"""
        if not self.exchange:
            raise RuntimeError("交易所未初始化")
        closed: list[str] = []
        errors: list[str] = []
        pairs = [
            (sym, d)
            for sym, legs in self.positions.items()
            for d, p in legs.items()
            if not p.is_flat
        ]
        for sym, direction in pairs:
            try:
                async with self.locks.setdefault(sym, asyncio.Lock()):
                    pos = self.positions.get(sym, {}).get(direction)
                    if pos is None or pos.is_flat:
                        continue
                    await self._do_close_leg(
                        sym, direction, event="CLOSE_MANUAL", reason=reason,
                    )
                    closed.append(f"{sym}:{direction}")
            except Exception as e:
                logger.exception("一键平仓失败 %s %s: %s", sym, direction, e)
                errors.append(f"{sym}:{direction}: {e}")
        self.bus.publish({"type": "runtime", "data": self.runtime_snapshot()})
        return {"closed": closed, "errors": errors, "count": len(closed)}

    async def _record_trade(self, symbol, side, position_side, event, qty, price,
                            margin, coid, raw, realized_pnl: float = 0.0):
        # 成交展示用真实下单时刻；K 线收盘时刻仅作元数据，避免全部显示成整点
        now_cn = datetime.now(TZ_CN)
        raw_out = dict(raw or {})
        raw_out["wall_ts"] = now_cn.isoformat(timespec="seconds")
        async with AsyncSessionLocal() as db:
            db.add(Trade(
                symbol=symbol, side=side, position_side=position_side, event=event,
                quantity=qty, price=price, notional=qty * price, margin=margin,
                realized_pnl=realized_pnl, client_order_id=coid, raw=raw_out,
            ))
            await db.commit()
        payload = {
            "type": "trade", "symbol": symbol, "event": event, "side": side,
            "position_side": position_side, "quantity": qty, "price": price,
            "margin": margin, "realized_pnl": realized_pnl,
            "ts": now_cn.isoformat(timespec="seconds"),
        }
        self.bus.publish(payload)

    async def _log(self, level: str, symbol: str, message: str, event: str = "engine"):
        logger.log(getattr(logging, level, logging.INFO), "[%s] %s", symbol, message)
        async with AsyncSessionLocal() as db:
            db.add(PositionLog(symbol=symbol, level=level, event=event, message=message))
            await db.commit()
        self.bus.publish({
            "type": "log", "level": level, "symbol": symbol, "message": message,
        })

    def runtime_snapshot(self) -> dict:
        return {
            "running": self.running,
            "booting": self.booting,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "timeframe": SIGNAL_TIMEFRAME,
            "leverage_mode": self.cfg_strategy.leverage_mode,
            "screener": {
                "last_at": self._last_screen_at.isoformat() if self._last_screen_at else None,
                "symbol_count": len(self.symbols),
                "refresh_count": self._screen_count,
            },
            "positions": [self._symbol_runtime(s) for s in self.symbols],
        }

    def _symbol_runtime(self, s: str) -> dict:
        legs = self.positions.get(s)
        if legs is None or s not in self.indicators:
            return {"symbol": s, "direction": "FLAT", "enabled": False,
                    "legs": [], "indicators": {}}
        leg_views = []
        total_pnl = 0.0
        total_margin = 0.0
        for d in DIRECTIONS:
            pos = legs[d]
            if pos.is_flat:
                continue
            snap = pos.snapshot()
            leg_views.append({
                "direction": d,
                "entry_price": snap["entry_price"],
                "quantity": snap["quantity"],
                "margin_used": snap["margin"],
                "unrealized_pnl": snap["unrealized_pnl"],
                "orders_count": snap["orders_count"],
                "tp6_fired": bool(snap["tp6_fired"]),
                "breakeven_armed": snap["breakeven_armed"],
                "legs_fired": snap["legs_fired"],
                "opened_at": snap["opened_at"],
            })
            total_pnl += snap["unrealized_pnl"]
            total_margin += snap["margin"]
        mark = max((legs[d].mark_price for d in DIRECTIONS), default=0.0)
        first = leg_views[0] if leg_views else None
        return {
            "symbol": s,
            "enabled": self.cfg_symbols.get(s, SymbolConfig(symbol=s)).enabled,
            # 兼容旧前端单腿展示；双腿时 legs 数组为准
            "direction": first["direction"] if first else "FLAT",
            "entry_price": first["entry_price"] if first else 0.0,
            "quantity": first["quantity"] if first else 0.0,
            "margin_used": total_margin,
            "unrealized_pnl": total_pnl,
            "mark_price": mark,
            "legs": leg_views,
            "indicators": self.indicators[s].snapshot(latest_price=mark or None),
        }
