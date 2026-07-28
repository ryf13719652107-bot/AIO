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


# ============ 策略子配置 ============
# 兼容旧指标订阅；新策略仅使用 STRATEGY_TIMEFRAMES
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "2h")
STRATEGY_TIMEFRAMES = ("1m", "3m", "5m")


class ScreeningConfig(BaseModel):
    """动态选币筛选条件（均可开关）。"""
    model_config = ConfigDict(extra="ignore")

    enable_volume: bool = True
    volume_min_usd: float = 5_000_000.0       # 500 万美元
    enable_mcap: bool = True
    mcap_min_usd: float = 8_000_000.0         # 800 万美元
    enable_mcap_max: bool = True
    mcap_max_usd: float = 6_000_000_000.0     # 60 亿美元
    enable_price: bool = True
    price_max_usd: float = 20.0
    refresh_hours: float = 1.0


class SideSignalConfig(BaseModel):
    """单方向 RSI + VOL 条件（已启用项 AND；全关则禁用该方向/阶段）。"""
    model_config = ConfigDict(extra="ignore")

    enable_rsi: bool = True
    rsi_threshold: float = 10.0
    enable_vol: bool = True
    vol_lookback: int = 30
    vol_mult: float = 7.0


class EntryConditionConfig(BaseModel):
    """开仓条件。"""
    model_config = ConfigDict(extra="ignore")

    enable_long: bool = True
    enable_short: bool = True
    long: SideSignalConfig = Field(
        default_factory=lambda: SideSignalConfig(
            enable_rsi=True, rsi_threshold=10.0,
            enable_vol=True, vol_lookback=30, vol_mult=7.0,
        )
    )
    short: SideSignalConfig = Field(
        default_factory=lambda: SideSignalConfig(
            enable_rsi=True, rsi_threshold=86.0,
            enable_vol=True, vol_lookback=30, vol_mult=7.0,
        )
    )


class AddLevelConfig(BaseModel):
    """单级加仓条件。"""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    long: SideSignalConfig = Field(
        default_factory=lambda: SideSignalConfig(
            enable_rsi=True, rsi_threshold=8.0,
            enable_vol=True, vol_lookback=30, vol_mult=10.0,
        )
    )
    short: SideSignalConfig = Field(
        default_factory=lambda: SideSignalConfig(
            enable_rsi=True, rsi_threshold=90.0,
            enable_vol=True, vol_lookback=30, vol_mult=10.0,
        )
    )


class AddConditionConfig(BaseModel):
    """两级顺序加仓（各最多一次）。"""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    level1: AddLevelConfig = Field(
        default_factory=lambda: AddLevelConfig(
            enabled=True,
            long=SideSignalConfig(
                enable_rsi=True, rsi_threshold=8.0,
                enable_vol=True, vol_lookback=30, vol_mult=10.0,
            ),
            short=SideSignalConfig(
                enable_rsi=True, rsi_threshold=90.0,
                enable_vol=True, vol_lookback=30, vol_mult=10.0,
            ),
        )
    )
    level2: AddLevelConfig = Field(
        default_factory=lambda: AddLevelConfig(
            enabled=True,
            long=SideSignalConfig(
                enable_rsi=True, rsi_threshold=6.0,
                enable_vol=True, vol_lookback=30, vol_mult=15.0,
            ),
            short=SideSignalConfig(
                enable_rsi=True, rsi_threshold=93.0,
                enable_vol=True, vol_lookback=30, vol_mult=15.0,
            ),
        )
    )


class ExitConfig(BaseModel):
    """止盈/止损：1m收盘武装后 TP1/SL2 实时；TP2 实时 RSI（live peek）。"""
    model_config = ConfigDict(extra="ignore")

    enable_tp1: bool = True
    tp1_profit_pct: float = 50.0            # 浮盈 ≥ 保证金×该比例 止盈

    enable_tp2: bool = True
    tp2_long_rsi: float = 85.0              # 多：实时 RSI ≥
    tp2_short_rsi: float = 15.0             # 空：实时 RSI ≤

    enable_sl1: bool = False                # 已废弃：保本（默认关闭）

    enable_sl2: bool = True
    sl2_margin_loss_pct: float = 10.0       # 整仓保证金浮亏 %


class StrategyParams(BaseModel):
    """RSI + 成交量策略参数（strategy_version=2）。"""
    model_config = ConfigDict(extra="ignore")

    strategy_version: int = 2
    timeframe: Literal["1m", "3m", "5m"] = "1m"
    position_pct: float = 2.0               # 每笔开/加仓：本金×该比例；满三级约 3×
    rsi_period: int = 6

    # 杠杆：follow=跟随交易所；manual=手动设置
    leverage_mode: Literal["follow", "manual"] = "follow"
    leverage: int = 20

    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)
    entry_conditions: EntryConditionConfig = Field(default_factory=EntryConditionConfig)
    add_conditions: AddConditionConfig = Field(default_factory=AddConditionConfig)
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


class SymbolRuntime(BaseModel):
    symbol: str
    enabled: bool
    direction: Literal["LONG", "SHORT", "FLAT"] = "FLAT"
    entry_price: float = 0.0
    quantity: float = 0.0
    margin_used: float = 0.0
    unrealized_pnl: float = 0.0
    mark_price: float = 0.0
