import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api import auth, config as config_api, control, ws
from app.config import get_settings
from app.core.security import hash_password
from app.database import AsyncSessionLocal, init_db
from app.models import User
from app.strategy.engine import StrategyEngine

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_admin()
    engine = StrategyEngine()
    app.state.engine = engine
    try:
        await engine.load_exchange_settings()
    except Exception as e:
        logging.getLogger("uvicorn").warning("加载交易所配置失败: %s", e)

    # 崩溃/重启自动恢复
    try:
        if await engine.was_running_before():
            logging.getLogger("uvicorn").warning(
                "检测到上次进程退出时引擎处于运行状态，正在自动恢复..."
            )
            await engine.start(persist=False)
    except Exception as e:
        logging.getLogger("uvicorn").error("自动恢复失败: %s", e)

    yield
    if engine.running:
        await engine.stop(persist=False)


async def _seed_admin():
    """首次启动写入管理员账户。"""
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
        if res.scalar_one_or_none() is None:
            db.add(User(username=settings.ADMIN_USERNAME, password_hash=hash_password(settings.ADMIN_PASSWORD)))
            await db.commit()


app = FastAPI(title="Binance Quant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(config_api.router, prefix="/api/config", tags=["config"])
app.include_router(control.router, prefix="/api/control", tags=["control"])
app.include_router(ws.router, prefix="/api/ws", tags=["ws"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# 前端静态托管
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "static"
if _FRONTEND_DIR.exists() and (_FRONTEND_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        target = _FRONTEND_DIR / full_path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(_FRONTEND_DIR / "index.html")
