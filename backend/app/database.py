from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    # 引入所有模型以注册到 metadata
    from app import models  # noqa: F401
    from sqlalchemy import text

    async with engine.begin() as conn:
        # 迁移：旧版（单方向、列式）position_state 结构不兼容 → 重建。
        # 旧仓不再从本地恢复，引擎启动后会从交易所 adopt 回收。
        def _migrate(sync_conn):
            try:
                rows = sync_conn.execute(
                    text("PRAGMA table_info(position_state)")
                ).fetchall()
            except Exception:
                return
            if not rows:
                return
            cols = {r[1] for r in rows}
            if "state_json" not in cols:
                sync_conn.execute(text("DROP TABLE position_state"))

        await conn.run_sync(_migrate)
        await conn.run_sync(Base.metadata.create_all)
