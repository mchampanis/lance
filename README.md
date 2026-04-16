# Lance

Multi-purpose Discord bot for the First Wave Survivors community.

## Features

- **Stream Alerts** - Announces when someone starts streaming in a voice channel, with a join link. Edits the announcement when the stream ends.
- **User Profiles** - `/lance settings` lets users set their Embark ID and timezone, stored globally per user (shared across servers).
- **Time Conversion** - React to any message with a clock emoji to get all times in it converted to your timezone via DM.
- **Giveaway Board** - Community item giveaway system with a live-updating board, queued claim/accept/decline workflow, hand over confirmation, milestone roles, and 5-day auto-expiry.
- **Countdowns** - Named event countdown timers using Discord's native timestamp rendering. Admins create, anyone can view.

## Getting Started

See [SETUP.md](SETUP.md) for full instructions on creating the Discord bot, inviting it, and running locally.

Quick start:

```bash
cp .env.example .env
# Edit .env with your bot token and guild IDs
uv sync
uv run python bot.py
```

## Deployment

Deployed to Fly.io. See [FLY-DEPLOY.md](FLY-DEPLOY.md) for details.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | Yes | - | Discord bot token |
| `GUILD_IDS` | Yes | - | Comma-separated guild IDs |
| `BOT_NAME` | No | `Lance` | Bot name shown in embed footers |
| `DB_PATH` | No | `lance.db` | SQLite database file path |
| `STREAM_CHANNEL_NAME` | No | `lounge` | Channel for stream announcements |
| `STREAM_ROLE_NAME` | No | `LFG Stream` | Role to ping on stream start |
| `TIMEZONE_HELPER_URL` | No | knowledgebase URL | Linked from `/lance settings` |
| `TIME_REACT_EMOJI` | No | `\N{MANTELPIECE CLOCK}` | Emoji that triggers time conversion |
| `GIVEAWAY_EXPIRY_HOURS` | No | `120` | Hours before giveaway items auto-expire |
| `GIVEAWAY_MILESTONES` | No | see `.env.example` | Milestone roles: `count:Role Name,...` |
| `TESTING` | No | `false` | Disables the self-claim guard (for local testing only) |
| `HEALTHCHECK_URL` | No | - | Healthchecks.io ping URL |

## License

MIT
