"""
WebSocket 实时推送：
- ?token=<JWT>     令牌通过 query 传递
- 推送类型: engine.status / trade / log / indicator / runtime（周期性）
"""

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.security import decode_access_token

router = APIRouter()


@router.websocket("/stream")
async def stream(ws: WebSocket, token: str = Query(...)):
    user = decode_access_token(token)
    if user is None:
        await ws.close(code=1008)
        return

    engine = ws.app.state.engine
    await ws.accept()
    q = engine.bus.subscribe()

    # 推送一次完整 runtime 快照作为初始化
    try:
        await ws.send_json({"type": "runtime", "data": engine.runtime_snapshot()})
        # 回放最近事件，避免刷新后「实时事件流」空白
        for ev in engine.bus.history():
            if ev.get("type") in ("log", "trade", "engine.status"):
                try:
                    await ws.send_json(ev)
                except Exception:
                    break
    except Exception:
        engine.bus.unsubscribe(q)
        return

    # 周期性快照任务
    push_interval = max(0.5, float(get_settings().RUNTIME_PUSH_SEC))

    async def push_periodic_runtime():
        while True:
            await asyncio.sleep(push_interval)
            try:
                await ws.send_json({"type": "runtime", "data": engine.runtime_snapshot()})
            except Exception:
                return

    periodic = asyncio.create_task(push_periodic_runtime())

    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        periodic.cancel()
        engine.bus.unsubscribe(q)
