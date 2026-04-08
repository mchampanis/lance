# Lance

Multi-purpose Discord bot for the First Wave Survivors community.

## Features

- **Stream Alerts** - Announces when someone starts streaming in a voice channel, with a join link. Edits the announcement when the stream ends.

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

Deployed to Fly.io. See [FLY-DEPLOY.md](FLY-DEPLOY.md) for details. Pushes to `main` auto-deploy via GitHub Actions.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | Yes | - | Discord bot token |
| `GUILD_IDS` | Yes | - | Comma-separated guild IDs |
| `BOT_NAME` | No | `Lance` | Bot name shown in embed footers |
| `STREAM_CHANNEL_NAME` | No | `lounge` | Channel for stream announcements |
| `STREAM_ROLE_NAME` | No | `LFG Stream` | Role to ping on stream start |
| `HEALTHCHECK_URL` | No | - | Healthchecks.io ping URL |

## License

MIT
