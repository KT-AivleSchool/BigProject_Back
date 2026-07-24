import asyncio
from dotenv import load_dotenv

load_dotenv()
from app.db.session import engine  # noqa: E402
from app.db.models.spatial import Base  # noqa: E402


async def init_models():
    print("Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Done!")


asyncio.run(init_models())
