import logging

import aiohttp
import discord
from discord.ext import commands, tasks

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("lance")

intents = discord.Intents.default()
intents.members = True
intents.presences = True


class LanceBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.load_extension("cogs.streams")

        for guild_id in config.GUILD_IDS:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Commands synced to guild %s", guild_id)

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        if config.HEALTHCHECK_URL and not self.heartbeat.is_running():
            self.heartbeat.start()

    @tasks.loop(minutes=5)
    async def heartbeat(self):
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(config.HEALTHCHECK_URL)
        except Exception:
            log.warning("Healthcheck ping failed", exc_info=True)


bot = LanceBot()
bot.run(config.BOT_TOKEN, log_handler=None)
