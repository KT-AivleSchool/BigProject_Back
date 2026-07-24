import asyncio
from app.db.session import engine
from sqlalchemy import text


async def check():
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT id, district_name FROM districts"))
        rows = res.fetchall()
        print("Districts:", rows)
        if not rows:
            await conn.execute(
                text(
                    "INSERT INTO districts (id, district_name, sig_cd) VALUES (1, '용산구', '11170')"
                )
            )
            print("Inserted Yongsan-gu")


asyncio.run(check())
