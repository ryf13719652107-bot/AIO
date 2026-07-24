from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.models import User
from app.schemas import ExchangeSettingsUpdate, ExchangeSettingsView, StrategyConfigPayload, SymbolConfig

router = APIRouter()


def _engine(request: Request):
    return request.app.state.engine


class SymbolEnabledPayload(BaseModel):
    enabled: bool


@router.get("", response_model=StrategyConfigPayload)
async def get_config(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    """只读配置：运行中返回内存快照，禁止 load_config 回写引擎（否则刷新页面会裁掉动态选币）。"""
    engine = _engine(request)
    from app.strategy.engine import StrategyEngine
    if engine.running or engine.booting:
        snap = engine.snapshot_config()
    else:
        await engine.load_config_from_db()
        snap = engine.snapshot_config()
    normalized = StrategyEngine._normalize_config_payload(snap.model_dump(by_alias=True))
    return StrategyConfigPayload.model_validate(normalized)


@router.put("", response_model=StrategyConfigPayload)
async def update_config(payload: StrategyConfigPayload, request: Request,
                        _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    old_strategy = engine.cfg_strategy.model_dump()
    old_symbols = set(engine.symbols)

    engine.apply_config(payload)
    await engine.save_config_to_db()

    new_strategy = engine.cfg_strategy.model_dump()
    need_restart = old_strategy != new_strategy or old_symbols != set(engine.symbols)
    if engine.running and need_restart:
        import logging
        logging.getLogger(__name__).info("策略参数变更，自动重启引擎")
        await engine.stop(persist=False)
        await engine.start(persist=True)

    return engine.snapshot_config()


@router.get("/exchange", response_model=ExchangeSettingsView)
async def get_exchange_settings(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    data = await engine.get_exchange_settings_view()
    return ExchangeSettingsView(
        api_key="",
        api_key_masked=data["api_key_masked"],
        has_api_key=data["has_api_key"],
        has_api_secret=data["has_api_secret"],
        paper_trading=data["paper_trading"],
        testnet=data["testnet"],
    )


@router.put("/exchange", response_model=ExchangeSettingsView)
async def update_exchange_settings(payload: ExchangeSettingsUpdate, request: Request,
                                   _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    try:
        data = await engine.update_exchange_settings(payload)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ExchangeSettingsView(
        api_key="",
        api_key_masked=data["api_key_masked"],
        has_api_key=data["has_api_key"],
        has_api_secret=data["has_api_secret"],
        paper_trading=data["paper_trading"],
        testnet=data["testnet"],
    )


@router.post("/symbol", response_model=StrategyConfigPayload)
async def upsert_symbol(symbol_cfg: SymbolConfig, request: Request,
                        _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    try:
        await engine.set_symbol_enabled(symbol_cfg.symbol, bool(symbol_cfg.enabled))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return engine.snapshot_config()


@router.put("/symbol/{symbol}/enabled")
async def update_symbol_enabled(symbol: str, payload: SymbolEnabledPayload, request: Request,
                                _user: Annotated[User, Depends(get_current_user)]):
    """运行中热切换币种开仓开关（禁开仓≠停止策略）。"""
    engine = _engine(request)
    try:
        await engine.set_symbol_enabled(symbol, payload.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"symbol": symbol.upper(), "enabled": payload.enabled}
