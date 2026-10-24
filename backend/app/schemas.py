from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============ Auth ============
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserInfo(BaseModel):
    id: int
    username: str
    model_config = ConfigDict(from_attributes=True)


# ============ 策略周期 ============
# 新策略：信号固定 5m；K线订阅 1m/5m/15m/30m/1h/2h
TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "2h")
STRATEGY_TIMEFRAMES = TIMEFRAMES
SIGNAL_TIMEFRAME = "5m"


# ============ 策略子配置 ============
class ScreeningConfig(BaseModel):
    """动态选币筛选条件（均可开关，数值前端可调）。"""
    model_config = ConfigDict(extra="ignore")

    enable_mcap: bool = True
    mcap_min_usd: float = 15_000_000.0        # 1500 万美元
    mcap_max_usd: float = 10_000_000_000.0    # 100 亿美元
    enable_price: bool = True
    price_max_usd: float = 30.0               # 币价 < 30
    enable_volume: bool = True
    volume_min_usd: float = 20_000_000.0      # 24h 成交额 2000 万
    volume_max_usd: float = 3_000_000_000.0   # 24h 成交额 30 亿
    enable_rsi_2h_high: bool = True           # 120 分钟 RSI(6) ≥ 阈值
    rsi_2h_high_threshold: float = 68.0        # 可选 40-80
    enable_rsi_2h_low: bool = True             # 120 分钟 RSI(6) ≤ 阈值
    rsi_2h_low_threshold: float = 45.0         # 可选 20-60
    refresh_hours: float = 1.0


class SideEntryConfig(BaseModel):
    """单方向开仓条件（启用项 AND；全关禁用该方向）。

    多头口径：5m EMA7>EMA25；EMA7&25 均>EMA99；RSI6<阈值；
    DIF>DEA；MACD柱<0 且连续3根缩小；高周期 EMA7>25 且均>99。
    空头完全镜像。
    """
    model_config = ConfigDict(extra="ignore")

    ema_5m: bool = True          # 5m EMA7 > EMA25（空: <）
    ema_5m_99: bool = True       # 5m EMA7&25 均 > EMA99（空: 均 <）
    rsi: bool = True             # 5m RSI(6) 阈值判定
    rsi_threshold: float = 65.0  # 多: RSI<阈值(1-85)；空: RSI>阈值(10-99)
    macd_dif: bool = True        # 5m DIF > DEA（空: <）
    macd_hist: bool = True       # 5m MACD柱<0且连续3根缩小（空: >0且连续3根缩小）
    ema_15m_cross: bool = True   # 15m EMA7 > EMA25（空: <）
    ema_15m_over99: bool = True  # 15m EMA7&25 均 > EMA99（空: 均 <）
    ema_30m_cross: bool = True   # 30m EMA7 > EMA25（空: <）
    ema_30m_over99: bool = True  # 30m EMA7&25 均 > EMA99（空: 均 <）
    ema_60m_cross: bool = True   # 60m EMA7 > EMA25（空: <）
    ema_60m_over99: bool = True  # 60m EMA7&25 均 > EMA99（空: 均 <）
    ema_120m_cross: bool = True  # 120m EMA7 > EMA25（空: <）
    ema_120m_over99: bool = True # 120m EMA7&25 均 > EMA99（空: 均 <）


class EntryConditionsConfig(BaseModel):
    """开仓条件（双向独立开关）。"""
    model_config = ConfigDict(extra="ignore")

    enable_long: bool = True
    enable_short: bool = True
    long: SideEntryConfig = Field(default_factory=lambda: SideEntryConfig(rsi_threshold=65.0))
    short: SideEntryConfig = Field(default_factory=lambda: SideEntryConfig(rsi_threshold=50.0))


class OrderPlanConfig(BaseModel):
    """下单计划：每单保证金 + 直接开仓/加仓档（各档互不影响，仅加仓档各一次）。

    档位（多单）：回踩 mark ≤ 5m EMA7 / EMA25 / EMA99 各加一单；
    15m / 30m 趋势条件（该周期收盘满足）各加一单。空单镜像。
    enable_first 关闭不影响其他档位入场（直接开仓只是入场方式之一）。
    """
    model_config = ConfigDict(extra="ignore")

    order_margin_usdt: float = Field(default=1.0, ge=1.0, le=100.0)  # 每单保证金 USDT
    enable_first: bool = True    # 满足开仓条件直接开仓（不打勾不影响其他档位入场）
    add_ema7: bool = True        # 回踩 ≤5m EMA7（空: ≥）
    add_ema25: bool = True       # 回踩 ≤5m EMA25，仅一次
    add_ema99: bool = True       # 回踩 ≤5m EMA99，仅一次
    add_15m: bool = True         # 15m EMA7&25 均>99（空镜像），仅一次
    add_30m: bool = True         # 30m 同上，仅一次


class TP6LevelConfig(BaseModel):
    """TP6 单档：某周期 RSI 阈值 → 平掉整笔订单总仓位的 close_pct%。"""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    rsi_threshold: float          # 多: ≥阈值；空: ≤阈值
    close_pct: float = Field(default=10.0, ge=1.0, le=100.0)


def _tp6_long() -> "TP6SideConfig":
    return TP6SideConfig(
        t5m=TP6LevelConfig(rsi_threshold=93.0, close_pct=10.0),
        t15m=TP6LevelConfig(rsi_threshold=95.0, close_pct=20.0),
        t30m=TP6LevelConfig(rsi_threshold=97.0, close_pct=20.0),
        t60m=TP6LevelConfig(rsi_threshold=97.0, close_pct=30.0),
        t60m_120m=TP6LevelConfig(rsi_threshold=96.0, close_pct=20.0),
    )


def _tp6_short() -> "TP6SideConfig":
    return TP6SideConfig(
        t5m=TP6LevelConfig(rsi_threshold=10.0, close_pct=10.0),
        t15m=TP6LevelConfig(rsi_threshold=7.0, close_pct=30.0),
        t30m=TP6LevelConfig(rsi_threshold=7.0, close_pct=20.0),
        t60m=TP6LevelConfig(rsi_threshold=8.0, close_pct=30.0),
        t60m_120m=TP6LevelConfig(rsi_threshold=8.0, close_pct=10.0),
    )


class TP6SideConfig(BaseModel):
    """TP6 分批止盈：5 档对应 5m / 15m / 30m / 60m / 60m&120m。"""
    model_config = ConfigDict(extra="ignore")

    t5m: TP6LevelConfig
    t15m: TP6LevelConfig
    t30m: TP6LevelConfig
    t60m: TP6LevelConfig
    t60m_120m: TP6LevelConfig


class TP6Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    long: TP6SideConfig = Field(default_factory=_tp6_long)
    short: TP6SideConfig = Field(default_factory=_tp6_short)


class ExitConfig(BaseModel):
    """止盈条件（先到先执行；TP1-6 全部按已收盘 K 线判定）。

    TP1: 1m EMA25<EMA99（多，空镜像）；TP2: 5m EMA7<EMA25；
    TP3: 5m EMA25<EMA99；TP4: 15m EMA7<EMA25；
    TP5: 5m VOL>前30根均量×倍数；TP6: 多周期 RSI 分批。
    TP6 任一档执行后触发保本损（剩余仓位 mark 触及开仓均价即全平）。
    硬止损：每笔订单浮亏率 ≥ 阈值 强制平仓（mark 实时盯价）。
    时间强平：持仓 ≥ 阈值小时 强制平仓。
    """
    model_config = ConfigDict(extra="ignore")

    enable_tp1: bool = True
    enable_tp2: bool = True
    enable_tp3: bool = True
    enable_tp4: bool = True
    enable_tp5: bool = True
    tp5_vol_mult: float = Field(default=10.0, ge=5.0, le=50.0)  # 放量倍数 5-50
    enable_tp6: bool = True
    tp6: TP6Config = Field(default_factory=TP6Config)
    enable_hard_stop: bool = True    # 硬止损：浮亏率 ≥ 阈值 强制平仓
    hard_stop_loss_pct: float = Field(default=20.0, ge=1.0, le=100.0)  # 1-100%
    enable_time_stop: bool = True    # 时间强平：持仓 ≥ 阈值小时 强制平仓
    time_stop_hours: float = Field(default=12.0, ge=6.0, le=24.0)  # 6-24 小时


class StrategyParams(BaseModel):
    """多周期 EMA/MACD/RSI 策略参数（strategy_version=3，双向持仓）。"""
    model_config = ConfigDict(extra="ignore")

    strategy_version: int = 3

    # 杠杆：follow=跟随交易所；manual=手动设置（5/10/20）
    leverage_mode: Literal["follow", "manual"] = "follow"
    leverage: int = 10

    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    entry: EntryConditionsConfig = Field(default_factory=EntryConditionsConfig)
    order_plan: OrderPlanConfig = Field(default_factory=OrderPlanConfig)
    exit: ExitConfig = Field(default_factory=ExitConfig)


# ============ 单币种配置 ============
class SymbolConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    enabled: bool = True


# ============ 全局配置 ============
class GlobalConfig(BaseModel):
    capital_source: Literal["env", "account"] = "env"
    capital_usdt: float = 200.0


class ExchangeSettingsView(BaseModel):
    """返回给前端的交易所配置（密钥脱敏）。"""
    api_key: str = ""
    api_key_masked: str = ""
    has_api_key: bool = False
    has_api_secret: bool = False
    paper_trading: bool = True
    testnet: bool = False


class ExchangeSettingsUpdate(BaseModel):
    """更新交易所配置。api_secret 空字符串表示不修改已有密钥。"""
    model_config = ConfigDict(extra="ignore")

    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    paper_trading: Optional[bool] = None
    testnet: Optional[bool] = None
    clear_credentials: bool = False


# ============ 配置 payload ============
class StrategyConfigPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    strategy: StrategyParams = Field(default_factory=StrategyParams)
    symbols: list[SymbolConfig] = Field(default_factory=list)


# ============ 引擎状态 ============
class EngineStatus(BaseModel):
    running: bool
    booting: bool = False
    started_at: Optional[datetime] = None


class PositionLegView(BaseModel):
    """单方向持仓视图（双向持仓的一腿）。"""
    direction: Literal["LONG", "SHORT"]
    entry_price: float = 0.0
    quantity: float = 0.0
    margin_used: float = 0.0
    unrealized_pnl: float = 0.0
    orders_count: int = 1        # 已开单数（含首仓与加仓）
    tp6_fired: bool = False      # 已触发 TP6 分批（保本损武装）
    breakeven_armed: bool = False


class SymbolRuntime(BaseModel):
    symbol: str
    enabled: bool
    direction: Literal["LONG", "SHORT", "FLAT"] = "FLAT"  # 兼容：单腿方向
    entry_price: float = 0.0
    quantity: float = 0.0
    margin_used: float = 0.0
    unrealized_pnl: float = 0.0
    mark_price: float = 0.0
    legs: list[PositionLegView] = Field(default_factory=list)
