"""Database schema and helpers for Lance.

Profiles are global per-user (not per-guild). The same user across multiple
servers shares one Embark ID and one timezone.

Giveaway items are per-guild. Claims link a claimer to an item.
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
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS giveaway_items (
            id         INTEGER PRIMARY KEY,
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            item_name  TEXT    NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            status     TEXT    NOT NULL DEFAULT 'available',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            gone_at    TIMESTAMP
        )
        """
    )
    await _ensure_giveaway_items_gone_at(db)
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS giveaway_claims (
            id         INTEGER PRIMARY KEY,
            item_id    INTEGER NOT NULL REFERENCES giveaway_items(id) ON DELETE CASCADE,
            claimer_id INTEGER NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS giveaway_board (
            guild_id   INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL
        )
        """
    )
    await db.commit()


async def _ensure_giveaway_items_gone_at(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(giveaway_items)") as cur:
        columns = {row[1] for row in await cur.fetchall()}

    if "gone_at" in columns:
        return

    await db.execute("ALTER TABLE giveaway_items ADD COLUMN gone_at TIMESTAMP")
    await db.execute(
        """
        UPDATE giveaway_items
        SET gone_at = CURRENT_TIMESTAMP
        WHERE status = 'gone' AND gone_at IS NULL
        """
    )


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


# -- Giveaway items -----------------------------------------------------------


async def create_item(
    db: aiosqlite.Connection, guild_id: int, user_id: int, item_name: str, quantity: int = 1,
) -> aiosqlite.Row:
    async with db.execute(
        """
        INSERT INTO giveaway_items (guild_id, user_id, item_name, quantity)
        VALUES (?, ?, ?, ?)
        RETURNING *
        """,
        (guild_id, user_id, item_name, quantity),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return row


async def get_item(db: aiosqlite.Connection, item_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM giveaway_items WHERE id = ?", (item_id,)) as cur:
        return await cur.fetchone()


async def get_available_items(db: aiosqlite.Connection, guild_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM giveaway_items WHERE guild_id = ? AND status = 'available' ORDER BY created_at DESC",
        (guild_id,),
    ) as cur:
        return await cur.fetchall()


async def get_user_items(db: aiosqlite.Connection, guild_id: int, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM giveaway_items WHERE guild_id = ? AND user_id = ? AND status = 'available' ORDER BY created_at DESC",
        (guild_id, user_id),
    ) as cur:
        return await cur.fetchall()


async def mark_item_gone(db: aiosqlite.Connection, item_id: int) -> None:
    await db.execute(
        "UPDATE giveaway_items SET status = 'gone', gone_at = CURRENT_TIMESTAMP WHERE id = ?",
        (item_id,),
    )
    # Decline any pending claims
    await db.execute(
        "UPDATE giveaway_claims SET status = 'declined' WHERE item_id = ? AND status = 'pending'",
        (item_id,),
    )
    await db.commit()


async def decrement_item(db: aiosqlite.Connection, item_id: int) -> int:
    """Decrement quantity by 1. Marks gone if it reaches 0. Returns new quantity."""
    await db.execute(
        "UPDATE giveaway_items SET quantity = quantity - 1 WHERE id = ? AND quantity > 0",
        (item_id,),
    )
    async with db.execute("SELECT quantity FROM giveaway_items WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    new_qty = row["quantity"] if row else 0
    if new_qty <= 0:
        await mark_item_gone(db, item_id)
    else:
        await db.commit()
    return new_qty


async def expire_old_items(db: aiosqlite.Connection, hours: int = 72) -> int:
    """Mark items older than `hours` as gone. Returns count of expired items."""
    cur = await db.execute(
        """
        UPDATE giveaway_items
        SET status = 'gone', gone_at = CURRENT_TIMESTAMP
        WHERE status = 'available'
          AND created_at < datetime('now', ? || ' hours')
        """,
        (f"-{hours}",),
    )
    count = cur.rowcount
    if count > 0:
        # Decline orphaned pending claims
        await db.execute(
            """
            UPDATE giveaway_claims SET status = 'declined'
            WHERE status = 'pending'
              AND item_id IN (SELECT id FROM giveaway_items WHERE status = 'gone')
            """
        )
        await db.commit()
    return count


async def purge_gone_items(db: aiosqlite.Connection, hours: int = 168) -> int:
    """Delete gone items older than `hours` (default 7 days). Returns count."""
    cur = await db.execute(
        """
        DELETE FROM giveaway_items
        WHERE status = 'gone'
          AND gone_at IS NOT NULL
          AND gone_at < datetime('now', ? || ' hours')
        """,
        (f"-{hours}",),
    )
    count = cur.rowcount
    if count > 0:
        await db.commit()
    return count


# -- Giveaway claims ----------------------------------------------------------


async def create_claim(db: aiosqlite.Connection, item_id: int, claimer_id: int) -> aiosqlite.Row:
    async with db.execute(
        "INSERT INTO giveaway_claims (item_id, claimer_id) VALUES (?, ?) RETURNING *",
        (item_id, claimer_id),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return row


async def get_claim(db: aiosqlite.Connection, claim_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM giveaway_claims WHERE id = ?", (claim_id,)) as cur:
        return await cur.fetchone()


async def get_pending_claims_for_item(db: aiosqlite.Connection, item_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM giveaway_claims WHERE item_id = ? AND status = 'pending' ORDER BY created_at",
        (item_id,),
    ) as cur:
        return await cur.fetchall()


async def get_accepted_claims_for_item(db: aiosqlite.Connection, item_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM giveaway_claims WHERE item_id = ? AND status = 'accepted' ORDER BY created_at",
        (item_id,),
    ) as cur:
        return await cur.fetchall()


async def get_declined_claims_for_item(db: aiosqlite.Connection, item_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM giveaway_claims WHERE item_id = ? AND status = 'declined' ORDER BY created_at",
        (item_id,),
    ) as cur:
        return await cur.fetchall()


async def has_active_claim(db: aiosqlite.Connection, item_id: int, claimer_id: int) -> bool:
    """Check if user already has a pending or accepted claim on this item."""
    async with db.execute(
        "SELECT 1 FROM giveaway_claims WHERE item_id = ? AND claimer_id = ? AND status IN ('pending', 'accepted')",
        (item_id, claimer_id),
    ) as cur:
        return await cur.fetchone() is not None


async def accept_claim(db: aiosqlite.Connection, claim_id: int) -> None:
    await db.execute(
        "UPDATE giveaway_claims SET status = 'accepted' WHERE id = ?", (claim_id,),
    )
    await db.commit()


async def decline_claim(db: aiosqlite.Connection, claim_id: int) -> None:
    await db.execute(
        "UPDATE giveaway_claims SET status = 'declined' WHERE id = ?", (claim_id,),
    )
    await db.commit()


# -- Giveaway board ------------------------------------------------------------


async def get_giveaway_board(db: aiosqlite.Connection, guild_id: int) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM giveaway_board WHERE guild_id = ?", (guild_id,),
    ) as cur:
        return await cur.fetchone()


async def set_giveaway_board(
    db: aiosqlite.Connection, guild_id: int, channel_id: int, message_id: int,
) -> None:
    await db.execute(
        """
        INSERT INTO giveaway_board (guild_id, channel_id, message_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id
        """,
        (guild_id, channel_id, message_id),
    )
    await db.commit()
