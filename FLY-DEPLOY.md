# Fly.io Deployment

## Quick Setup

### 1. Create app and volume

```bash
fly launch --no-deploy
fly volumes create lance_data --region ams --size 1
```

When prompted, accept the app name from `fly.toml` (`lance`) and the `ams` region. The volume holds the SQLite database for user profiles.

### 2. Set secrets

These replace your local `.env` file. Fly injects them as environment variables.

```bash
fly secrets set BOT_TOKEN="your-token"
fly secrets set GUILD_IDS="id1,id2"
```

### 3. Deploy

```powershell
./run.ps1 deploy
```

This writes the current git short SHA to a `COMMIT` file (copied into the image) and runs `fly deploy`. To verify which SHA is running on the Fly machine afterwards:

```powershell
./run.ps1 'deploy:check'
```

(Quotes are required around `deploy:check` in PowerShell because of the colon.)

## Configuration

Non-secret config can go in `fly.toml` under `[env]` if needed. Secrets are set via `fly secrets set`.

| Variable | Where | Description |
|---|---|---|
| `BOT_TOKEN` | `fly secrets` | Discord bot token |
| `GUILD_IDS` | `fly secrets` | Discord server ID(s), comma-separated |
| `BOT_NAME` | `fly.toml [env]` | Bot name in embed footers (default: Lance) |
| `DB_PATH` | `fly.toml [env]` | SQLite path (must be on mounted volume) |
| `STREAM_CHANNEL_NAME` | `fly.toml [env]` | Announcement channel name (default: lounge) |
| `STREAM_ROLE_NAME` | `fly.toml [env]` | Role to ping on stream start (default: LFG Stream) |
| `TIMEZONE_HELPER_URL` | `fly.toml [env]` | URL to the timezone finder helper page |
| `TIME_REACT_EMOJI` | `fly.toml [env]` | Emoji that triggers time conversion (default: clock) |
| `GIVEAWAY_EXPIRY_HOURS` | `fly.toml [env]` | Hours before giveaway items auto-expire (default: 120) |
| `HEALTHCHECK_URL` | `fly secrets` | Healthchecks.io ping URL (optional) |

## Key Details

- **No `[http_service]`** -- this is a worker process, not a web server. Fly won't run HTTP health checks or auto-stop it.
- **Volume** is pinned to `ams` region. The app's `primary_region` must match.
- **Single instance only** -- SQLite doesn't support concurrent writers across machines, and only one bot process can hold the Discord gateway connection.
- **Auto-restart** -- if the bot crashes, Fly restarts it automatically.
- **`load_dotenv()`** is a no-op when no `.env` file exists. Fly secrets are already env vars.

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

```powershell
./run.ps1 deploy            # Deploy latest changes (writes COMMIT, then fly deploy)
./run.ps1 'deploy:check'    # Check which git SHA is running on the Fly machine
```

```bash
fly status              # Check app status
fly ssh console         # SSH into the machine
fly secrets list        # List set secrets
fly secrets set K=V     # Set a secret
fly secrets unset X     # Remove a secret
fly volumes list        # List volumes
```
