# Fly.io Deployment

## Quick Setup

### 1. Create app

```bash
fly launch --no-deploy
```

When prompted, accept the app name from `fly.toml` (`lance`) and the `ams` region. No database or volume needed.

### 2. Set secrets

These replace your local `.env` file. Fly injects them as environment variables.

```bash
fly secrets set BOT_TOKEN="your-token"
fly secrets set GUILD_IDS="id1,id2"
```

### 3. Deploy

```bash
fly deploy
```

## Configuration

Non-secret config can go in `fly.toml` under `[env]` if needed. Secrets are set via `fly secrets set`.

| Variable | Where | Description |
|---|---|---|
| `BOT_TOKEN` | `fly secrets` | Discord bot token |
| `GUILD_IDS` | `fly secrets` | Discord server ID(s), comma-separated |
| `BOT_NAME` | `fly.toml [env]` | Bot name in embed footers (default: Lance) |
| `STREAM_CHANNEL_NAME` | `fly.toml [env]` | Announcement channel name (default: lounge) |
| `STREAM_ROLE_NAME` | `fly.toml [env]` | Role to ping on stream start (default: LFG Stream) |
| `HEALTHCHECK_URL` | `fly secrets` | Healthchecks.io ping URL (optional) |

## Key Details

- **No `[http_service]`** -- this is a worker process, not a web server. Fly won't run HTTP health checks or auto-stop it.
- **No volume** -- Lance has no database or persistent storage requirements.
- **Single instance only** -- only one bot process can hold the Discord gateway connection.
- **Auto-restart** -- if the bot crashes, Fly restarts it automatically.
- **`load_dotenv()`** is a no-op when no `.env` file exists. Fly secrets are already env vars.

## CI/CD

The GitHub Actions workflow at `.github/workflows/fly-deploy.yml` deploys on every push to `main`. To set it up:

1. Get a Fly API token: `fly tokens create deploy -x 999999h`
2. Add it as a GitHub repository secret named `FLY_API_TOKEN` at:
   `https://github.com/mchampanis/lance/settings/secrets/actions`

## Monitoring

Set `HEALTHCHECK_URL` to a [Healthchecks.io](https://healthchecks.io) ping URL to get notified if the bot goes down. The bot pings every 5 minutes. Recommended HC schedule: 10-minute period, 10-minute grace.

```bash
fly secrets set HEALTHCHECK_URL="https://hc-ping.com/your-uuid"
```

## Logs

```bash
fly logs                  # Stream live logs
fly logs --app lance  # Explicit app name
```

Logs are also available in the Fly dashboard at https://fly.io/apps/lance/monitoring.

## Useful Commands

```bash
fly status              # Check app status
fly deploy              # Deploy latest changes
fly ssh console         # SSH into the machine
fly secrets list        # List set secrets
fly secrets set K=V     # Set a secret
fly secrets unset X     # Remove a secret
```
