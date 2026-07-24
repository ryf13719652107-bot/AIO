from datetime import timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.core.deps import get_current_user
from app.database import AsyncSessionLocal
from app.models import Trade, User
from app.schemas import EngineStatus

router = APIRouter()
TZ_CN = ZoneInfo("Asia/Shanghai")


def _engine(request: Request):
    return request.app.state.engine


@router.get("/status", response_model=EngineStatus)
async def status_(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    return EngineStatus(
        running=engine.running,
        booting=getattr(engine, "booting", False),
        started_at=engine.started_at,
    )


@router.post("/start")
async def start(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    if engine.running or getattr(engine, "booting", False):
        return {"ok": True, "message": "已在运行或正在启动"}
    try:
        await engine.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("策略启动失败")
        msg = str(e).strip() or repr(e)
        raise HTTPException(status_code=500, detail=f"启动失败: {msg}") from e
    return {"ok": True, "message": "已开始启动（后台选币与初始化中）"}


@router.post("/stop")
async def stop(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    if not engine.running:
        return {"ok": True, "message": "已停止"}
    await engine.stop()
    return {"ok": True, "message": "已停止"}


@router.get("/runtime")
async def runtime(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    engine = _engine(request)
    return engine.runtime_snapshot()


@router.get("/balance")
async def balance(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    """查询币安账户 USDT 可用余额。"""
    engine = _engine(request)
    return await engine.query_account_balance()


@router.get("/trades")
async def list_trades(
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(200, ge=1, le=500),
):
    """最近成交记录（刷新页面可回填）。"""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Trade).order_by(Trade.id.desc()).limit(limit)
            )
        ).scalars().all()
    items = []
    for r in rows:
        raw = r.raw or {}
        kline_ts = raw.get("kline_close_ts") if isinstance(raw, dict) else None
        if kline_ts:
            ts_out = kline_ts
        else:
            created = r.created_at
            if created is not None and created.tzinfo is None:
                # 历史写入为 UTC naive
                created = created.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
            elif created is not None:
                created = created.astimezone(TZ_CN)
            ts_out = created.isoformat(timespec="seconds") if created else None
        items.append({
            "type": "trade",
            "id": r.id,
            "symbol": r.symbol,
            "side": r.side,
            "position_side": r.position_side,
            "event": r.event,
            "quantity": r.quantity,
            "price": r.price,
            "margin": r.margin,
            "realized_pnl": r.realized_pnl,
            "ts": ts_out,
        })
    return {"items": items, "count": len(items)}


@router.get("/logs")
async def list_logs(
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(200, ge=1, le=500),
):
    """最近事件日志（刷新页面可回填实时事件流）。"""
    from app.models import PositionLog

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(PositionLog).order_by(PositionLog.id.desc()).limit(limit)
            )
        ).scalars().all()
    items = []
    for r in rows:
        created = r.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc).astimezone(TZ_CN)
        elif created is not None:
            created = created.astimezone(TZ_CN)
        items.append({
            "type": "log",
            "id": r.id,
            "level": r.level,
            "symbol": r.symbol,
            "event": r.event,
            "message": r.message,
            "ts": created.isoformat(timespec="seconds") if created else None,
        })
    return {"items": items, "count": len(items)}


@router.post("/close/{symbol}")
async def emergency_close(symbol: str, request: Request,
                          _user: Annotated[User, Depends(get_current_user)]):
    """紧急平仓单币种。"""
    engine = _engine(request)
    if not engine.running:
        raise HTTPException(status_code=400, detail="引擎未运行")
    sym = symbol.upper()
    if sym not in engine.positions:
        raise HTTPException(status_code=404, detail="未知交易对")
    pos = engine.positions[sym]
    if pos.is_flat:
        return {"ok": True, "message": "当前无持仓"}
    async with engine.locks[sym]:
        await engine._do_close(sym, event="CLOSE_MANUAL", reason="手动紧急平仓")
    return {"ok": True}


@router.post("/close-all")
async def close_all(request: Request, _user: Annotated[User, Depends(get_current_user)]):
    """一键平掉全部持仓。"""
    engine = _engine(request)
    if not engine.running:
        raise HTTPException(status_code=400, detail="引擎未运行")
    try:
        result = await engine.close_all_positions(reason="手动一键平仓")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"一键平仓失败: {e}") from e
    return {"ok": True, **result}
