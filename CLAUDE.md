# Lance - Multi-purpose Discord Bot

## Overview

Lance is a Swiss army knife Discord bot for the First Wave Survivors community (Arc Raiders). It handles stream announcements, and will eventually handle knowledgebase notifications and timezone conversions.

## Stack

- Python 3.11+ with discord.py 2.5+
- uv for package management
- Cogs-based architecture (one cog per feature domain)
- Config via `.env` with python-dotenv

## Running

```bash
uv sync
uv run python bot.py
```

## Architecture

- `bot.py` - Entry point, bot lifecycle, healthcheck
- `config.py` - All configuration from environment variables
- `cogs/` - Feature modules, one per domain:
  - `streams.py` - Voice channel stream announcements

## Required Discord Bot Permissions

- Send Messages
- Embed Links
- Create Instant Invite (for VC join links)
- Read Message History

## Required Intents

- Members (privileged; for member cache population)
- Presences (privileged; enabled but may not be needed -- see Notes)
- Voice States (for `on_voice_state_update` and `self_stream`; included in `Intents.default()`)

## Notes

- The Presences intent is privileged and must be enabled in the Discord Developer Portal under Bot -> Privileged Gateway Intents.
- Members intent is also privileged and must be enabled there.
- Stream detection relies on `VoiceState.self_stream` from the `VOICE_STATE_UPDATE` gateway event, which requires the Voice States intent (included in `Intents.default()`). The Presences intent is enabled but not used by the streams cog -- it may be droppable, but needs testing first.
- In-memory `active_streams` dict is lost on bot restart. Any streams active at restart time will never get their "stream ended" edit.
