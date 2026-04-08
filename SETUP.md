# Setup Guide

## 1. Create a Discord Bot Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, name it "Lance" (or whatever you like)
3. Go to **Bot** in the left sidebar
4. Click **Reset Token** and copy the token -- this is your `BOT_TOKEN`
5. Under **Privileged Gateway Intents**, enable:
   - **Presence Intent** (required for stream detection)
   - **Server Members Intent** (required for member lookup)

## 2. Invite the Bot to Your Server

Use this OAuth2 URL, replacing `CLIENT_ID` with your application's client ID (found on the **General Information** page):

```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=83969
```

This grants the bot these permissions:
- Create Instant Invite (for VC join links)
- Send Messages
- Embed Links
- Read Message History

## 3. Server Preparation

Create a role called **LFG Stream** (or whatever you set `STREAM_ROLE_NAME` to). Members who want stream notifications should self-assign this role.

The bot will post announcements to a channel called **#lounge** by default (configurable via `STREAM_CHANNEL_NAME`). Make sure the bot has access to that channel.

## 4. Local Development

```bash
cp .env.example .env
# Edit .env with your bot token and guild IDs
uv sync
uv run python bot.py
```

## 5. Fly.io Deployment

See [FLY-DEPLOY.md](FLY-DEPLOY.md) for production deployment instructions.
