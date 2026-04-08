"""Database schema and helpers for Lance.

Profiles are global per-user (not per-guild). The same user across multiple
servers shares one Embark ID and one timezone.
"""

import aiosqlite


async def init_db(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            user_id    INTEGER PRIMARY KEY,
            embark_id  TEXT,
            timezone   TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.commit()


async def get_profile(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT user_id, embark_id, timezone, updated_at FROM profiles WHERE user_id = ?",
        (user_id,),
    ) as cur:
        return await cur.fetchone()


async def set_embark_id(db: aiosqlite.Connection, user_id: int, embark_id: str) -> None:
    await db.execute(
        """
        INSERT INTO profiles (user_id, embark_id, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            embark_id = excluded.embark_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, embark_id),
    )
    await db.commit()


async def set_timezone(db: aiosqlite.Connection, user_id: int, timezone: str) -> None:
    await db.execute(
        """
        INSERT INTO profiles (user_id, timezone, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            timezone = excluded.timezone,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, timezone),
    )
    await db.commit()


async def clear_embark_id(db: aiosqlite.Connection, user_id: int) -> None:
    await db.execute(
        "UPDATE profiles SET embark_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (user_id,),
    )
    await db.commit()


async def clear_timezone(db: aiosqlite.Connection, user_id: int) -> None:
    await db.execute(
        "UPDATE profiles SET timezone = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (user_id,),
    )
    await db.commit()
