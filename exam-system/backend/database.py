import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./exam.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    from models import Base as ModelBase
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA cache_size=10000"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(ModelBase.metadata.create_all)
        # Non-destructive migrations for new columns
        for col_sql in [
            "ALTER TABLE sessions ADD COLUMN session_code TEXT",
            "ALTER TABLE sessions ADD COLUMN class_name TEXT",
            "ALTER TABLE sessions ADD COLUMN category TEXT",
            "ALTER TABLE sessions ADD COLUMN folder_id TEXT",
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass  # Column already exists
