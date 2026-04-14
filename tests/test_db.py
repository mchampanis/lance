import unittest

import aiosqlite

import db


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = await aiosqlite.connect(":memory:")
        self.conn.row_factory = aiosqlite.Row

    async def asyncTearDown(self):
        await self.conn.close()

    async def test_mark_item_gone_sets_gone_at(self):
        await db.init_db(self.conn)
        item = await db.create_item(
            self.conn,
            guild_id=1,
            user_id=2,
            item_name="Motor",
            quantity=1,
        )

        await db.mark_item_gone(self.conn, item["id"])

        stored = await db.get_item(self.conn, item["id"])
        self.assertEqual(stored["status"], "gone")
        self.assertIsNotNone(stored["gone_at"])

    async def test_purge_gone_items_uses_gone_at_not_created_at(self):
        await db.init_db(self.conn)
        item = await db.create_item(
            self.conn,
            guild_id=1,
            user_id=2,
            item_name="Plate",
            quantity=1,
        )

        await self.conn.execute(
            """
            UPDATE giveaway_items
            SET status = 'gone',
                created_at = datetime('now', '-30 days'),
                gone_at = datetime('now', '-1 day')
            WHERE id = ?
            """,
            (item["id"],),
        )
        await self.conn.commit()

        purged = await db.purge_gone_items(self.conn, hours=24 * 7)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(await db.get_item(self.conn, item["id"]))

    async def test_init_db_migrates_legacy_giveaway_items_schema(self):
        await self.conn.execute(
            """
            CREATE TABLE giveaway_items (
                id         INTEGER PRIMARY KEY,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                item_name  TEXT    NOT NULL,
                quantity   INTEGER NOT NULL DEFAULT 1,
                status     TEXT    NOT NULL DEFAULT 'available',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.commit()

        await db.init_db(self.conn)

        async with self.conn.execute("PRAGMA table_info(giveaway_items)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}

        self.assertIn("gone_at", columns)


if __name__ == "__main__":
    unittest.main()
