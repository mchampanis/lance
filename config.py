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
GIVEAWAY_EXPIRY_HOURS = int(os.environ.get("GIVEAWAY_EXPIRY_HOURS", "120"))

# Milestone roles: "count:Role Name,count:Role Name,..."
# Roles must already exist in the guild. Bot awards highest earned, removes lower ones.
_DEFAULT_MILESTONES = (
    "1:Freebie Giver,"
    "5:Freebie Apprentice,"
    "10:Freebie Enthusiast,"
    "50:Freebie Champion,"
    "100:Freebie Master"
)
GIVEAWAY_MILESTONES: list[tuple[int, str]] = []
for _entry in os.environ.get("GIVEAWAY_MILESTONES", _DEFAULT_MILESTONES).split(","):
    _entry = _entry.strip()
    if ":" in _entry:
        _count, _name = _entry.split(":", 1)
        GIVEAWAY_MILESTONES.append((int(_count.strip()), _name.strip()))
GIVEAWAY_MILESTONES.sort(key=lambda m: m[0])

# Healthchecks.io ping URL (optional)
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL")
