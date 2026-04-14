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

# Time conversion reaction trigger emoji (Unicode name or custom emoji string)
TIME_REACT_EMOJI = os.environ.get("TIME_REACT_EMOJI", "\N{CLOCK FACE THREE OCLOCK}")

# Giveaways
GIVEAWAY_EXPIRY_HOURS = int(os.environ.get("GIVEAWAY_EXPIRY_HOURS", "72"))

# Healthchecks.io ping URL (optional)
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")
