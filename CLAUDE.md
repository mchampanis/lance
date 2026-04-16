# Lance - Multi-purpose Discord Bot

## Overview

Lance is a Swiss army knife Discord bot for the First Wave Survivors community (Arc Raiders). It handles stream announcements, user profiles, timezone conversions, a community giveaway board, and event countdown timers.

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

- `bot.py` - Entry point, bot lifecycle, healthcheck, database init
- `config.py` - All configuration from environment variables
- `db.py` - SQLite schema and helpers (profiles, giveaway items/claims/board/stats, countdowns)
- `cogs/` - Feature modules, one per domain:
  - `streams.py` - Voice channel stream announcements
  - `profiles.py` - `/lance settings` user profiles (Embark ID, timezone)
  - `timeconvert.py` - Clock emoji reaction -> DM with times converted to your timezone
  - `giveaways.py` - Community item giveaway board with claim/accept/decline workflow
  - `countdowns.py` - Named event countdown timers using Discord timestamps

## Slash command structure

All user-facing commands live under the `/lance` group, defined in `cogs/__init__.py` and shared across cogs:
- `/lance help` (profiles cog)
- `/lance settings` (profiles cog)
- `/lance profile @user` (profiles cog)
- `/lance give` (giveaways cog)
- `/lance giveaway-setup` (giveaways cog, admin only)
- `/lance countdown [name]` (countdowns cog; admins see Create/Delete buttons)
- **View Profile** context menu (right-click a user -> Apps; profiles cog)

The streams and timeconvert cogs are purely event-driven (no slash commands).

## Required Discord Bot Permissions

- Send Messages
- Embed Links
- Create Instant Invite (for VC join links)
- Read Message History
- Manage Roles (for giveaway milestone role awards)

## Required Intents

- Members (privileged; for member cache population)
- Presences (privileged; required for detecting the game being streamed)
- Message Content (privileged; for reading message text in time conversion reactions)
- Voice States (for `on_voice_state_update` and `self_stream`; included in `Intents.default()`)

## Notes

- The Presences intent is privileged and must be enabled in the Discord Developer Portal under Bot -> Privileged Gateway Intents.
- Members intent is also privileged and must be enabled there.
- Stream detection relies on `VoiceState.self_stream` from the `VOICE_STATE_UPDATE` gateway event, which requires the Voice States intent (included in `Intents.default()`). The Presences intent is used to read `member.activities` and show the game name in stream announcements.
- In-memory `active_streams` dict is lost on bot restart. Any streams active at restart time will never get their "stream ended" edit.
