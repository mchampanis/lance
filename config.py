import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
GUILD_IDS = [int(gid.strip()) for gid in os.environ["GUILD_IDS"].split(",")]

# Bot identity
BOT_NAME = os.environ.get("BOT_NAME", "Lance")

# Database
DB_PATH = os.environ.get("DB_PATH", "lance.db")

# Stream announcements
STREAM_CHANNEL_NAME = os.environ.get("STREAM_CHANNEL_NAME", "lounge")
STREAM_ROLE_NAME = os.environ.get("STREAM_ROLE_NAME", "LFG Stream")

# Timezone helper page (linked from /lance settings)
TIMEZONE_HELPER_URL = os.environ.get(
    "TIMEZONE_HELPER_URL",
    "https://guides.firstwavesurvivors.com/tools/timezone",
)

# Healthchecks.io ping URL (optional)
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")
