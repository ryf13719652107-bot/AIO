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
        await conn.run_sync(Base.metadata.create_all)
        # SQLite 兼容迁移：为 position_state 补齐新列
        def _migrate(sync_conn):
            try:
                rows = sync_conn.execute(text("PRAGMA table_info(position_state)")).fetchall()
            except Exception:
                return
            cols = {r[1] for r in rows}
            alters = []
            if "add_count" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN add_count INTEGER DEFAULT 0")
            if "add_blocked" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN add_blocked BOOLEAN DEFAULT 0")
            if "pending_baseline" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN pending_baseline BOOLEAN DEFAULT 0")
            if "baseline_armed" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN baseline_armed BOOLEAN DEFAULT 0")
            if "baseline_price" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN baseline_price FLOAT DEFAULT 0")
            if "baseline_pnl" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN baseline_pnl FLOAT DEFAULT 0")
            if "baseline_open_ms" not in cols:
                alters.append("ALTER TABLE position_state ADD COLUMN baseline_open_ms INTEGER DEFAULT 0")
            for sql in alters:
                sync_conn.execute(text(sql))

        await conn.run_sync(_migrate)
