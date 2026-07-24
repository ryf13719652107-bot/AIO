from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 3003
    APP_DEBUG: bool = False

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

    JWT_SECRET: str = "please-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin@2026"

    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool = False
    # 行情订阅模式：WS（默认，低延迟）/ REST（轮询，兜底；WS 被墙时用）
    MARKET_FEED: str = "ws"
    # REST 模式下 markPrice 轮询间隔（秒）
    MARK_POLL_SEC: float = 1.0
    # WS 推给前端的 runtime 快照间隔（秒）
    RUNTIME_PUSH_SEC: float = 2.0

    CAPITAL_SOURCE: str = "env"  # "env" | "account"
    CAPITAL_USDT: float = 200.0
    LEVERAGE: int = 20

    PAPER_TRADING: bool = True

    COINGECKO_API_BASE: str = "https://api.coingecko.com/api/v3"
    COINGECKO_TIMEOUT: float = 30.0

    # 币安期货行情 WS（部分地区 fstream.binance.com 无数据，可用 /market 前缀）
    BINANCE_WS_BASE: str = "wss://fstream.binance.com/market/ws"
    BINANCE_WS_STREAM: str = "wss://fstream.binance.com/market/stream"


@lru_cache
def get_settings() -> Settings:
    return Settings()
